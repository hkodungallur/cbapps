#!/usr/bin/python

import os
import sys
import signal
import logging
import json

from couchbase.bucket import Bucket
from couchbase.n1ql import N1QLQuery
from couchbase.views.params import Query
from couchbase.bucket import LOCKMODE_WAIT
from couchbase.exceptions import CouchbaseError, KeyExistsError, NotFoundError
from couchbase.views.iterator import RowProcessor


logger = logging.getLogger()

class buildDB(object):
    def __init__(self, bucket):
        self.bucket = bucket
        self.db = Bucket(bucket, lockmode=LOCKMODE_WAIT)

        self.q_get_latest_build = '''
                                  SELECT MAX(build_num) from `build-history` 
                                  WHERE 
                                    version = '4.5.0'  AND
                                    type = 'top_level_build'
                                  '''

        self.q_get_latest_good = '''
                                  SELECT MAX(build_num) from `build-history` 
                                  WHERE
                                    version = '4.5.0'  AND
                                    type = 'top_level_build' AND
                                    product_branch = '{}' AND
                                    failed = []   AND
                                    incomplete = [] 
                                 '''

        self.q_short_history = '''
                                  SELECT build_num, job_build_num, passed, failed, incomplete, timestamp from `build-history` 
                                  WHERE 
                                    version = '4.5.0' AND type = 'top_level_build'
                                    {}
                                    ORDER BY build_num DESC
                                    LIMIT {}
                               '''
        self.q_long_history = '''
                                  SELECT b.build_num, b.timestamp, c, d 
                                  FROM `build-history` AS b 
                                  LEFT NEST `build-history` AS c ON KEYS b.commits 
                                  LEFT NEST `build-history` AS d ON KEYS ARRAY_CONCAT(b.incomplete, ARRAY_CONCAT(b.failed, b.passed))
                                  WHERE 
                                    b.type = 'top_level_build'
                                    {}
                                  ORDER BY b.build_num DESC LIMIT {}
                             '''
        self.q_build_history = '''
                                  SELECT b.build_num, b.timestamp, c, d 
                                  FROM `build-history` AS b 
                                  LEFT NEST `build-history` AS c ON KEYS b.commits 
                                  LEFT NEST `build-history` AS d ON KEYS ARRAY_CONCAT(b.incomplete, ARRAY_CONCAT(b.failed, b.passed))
                                  WHERE b.type = 'top_level_build' AND b.build_num = {}
                             '''

    def get_builds_with_details(self, how_many=20):
        latest_str = self.db.n1ql_query(self.q_get_latest_build).get_single_result()["$1"]
        latest = int(latest_str)
        history = latest - how_many;

    def get_recent_builds(self, release, rel_line, how_many=5):
        rel_line_info = self.get_release_line_info(release, rel_line)
        q_substr = ''
        last_good = "unknown"
        if rel_line_info:
            query = self.q_get_latest_good.format(rel_line_info['product_branch'])
            last_good = self.db.n1ql_query(query).get_single_result()["$1"]
            q_substr = q_substr + " AND product_branch = '%s'" %rel_line_info['product_branch']
            if rel_line_info.has_key('start'):
                q_substr = q_substr + ' AND build_num >= %d' %rel_line_info['start']
            if rel_line_info.has_key('end'):
                q_substr = q_substr + ' AND build_num < %d' %rel_line_info['end']

        query = self.q_short_history.format(q_substr, str(how_many))
        q2 = N1QLQuery(query)
        rows = {}
        for row in self.db.n1ql_query(q2):
            result = "success"
            if row['incomplete']:
                result = "building"
            else:
                if row['failed']:
                    result = "failure"
            all_distro = row['failed'] + row['incomplete'] + row['passed']
            #q2 = N1QLQuery("SELECT distro, url from `build-history` WHERE Meta(`build-history`).id in $a", a=all_distro)
            #urls = []
            #for url in self.db.n1ql_query(q2):
            #    urls.append((url['distro'], url['url']))
            rows[row['build_num']] = {
                                       'timestamp': float(row['timestamp'])/1000,
                                       'url': "http://server.jenkins.couchbase.com/job/watson-build/%s" %row['job_build_num'],
                                       'result': result,
                                    }
        return last_good, rows

    def get_long_history(self, release, rel_line=None, how_many=25):
        q_substr = ''
        if rel_line:
            rel_line_info = self.get_release_line_info(release, rel_line)
            if rel_line_info:
                q_substr = q_substr + " AND b.product_branch = '%s'" %rel_line_info['product_branch']
                if rel_line_info.has_key('start'):
                    q_substr = q_substr + ' AND b.build_num >= %d' %rel_line_info['start']
                if rel_line_info.has_key('end'):
                    q_substr = q_substr + ' AND b.build_num < %d' %rel_line_info['end']

        query = self.q_long_history.format(q_substr, str(how_many))
        nq = N1QLQuery(query)
        ret = []
        for row in self.db.n1ql_query(nq):
            ret.append(row)
        return ret

    def get_build_history(self, build_num):
        query = self.q_build_history.format(int(build_num))
        nq = N1QLQuery(query)
        ret = []
        for row in self.db.n1ql_query(nq):
            ret.append(row)
        return ret

    def doc_exists(self, doc_id):
        try:
            result = self.db.get(doc_id)
        except CouchbaseError as e:
            return False

        return result

    def get_all_releases(self):
        rel_obj = self.doc_exists('all-releases')
        ret = []
        if rel_obj:
            ret = rel_obj.value.keys()
        return ret

    def get_release_lines(self, release, only_active=True):
        rel_obj = self.doc_exists('all-releases')
        ret = []
        if rel_obj:
            rel_info = rel_obj.value.get(release, {})
            if rel_info:
                rel_lines = rel_info.get('release_lines', [])
                for line in rel_lines:
                    if only_active and (not line['active']):
                        continue
                    ret.append(line['name'])
        return ret

    def get_release_line_info(self, release, rel_line):
        rel_obj = self.doc_exists('all-releases')
        if rel_obj:
            rel_info = rel_obj.value.get(release, {})
            if rel_info:
                rel_lines = rel_info.get('release_lines', [])
                for line in rel_lines:
                    if line['name'] == rel_line:
                        return line
        return {}

    def add_type(self):
        q = "SELECT meta(`build-history`).id, * from `build-history`"
        nq = N1QLQuery(q)
        for row in self.db.n1ql_query(nq):
            id = row['id']
            r = row['build-history']
            if r.has_key('manifest_sha'):
                r['type'] = 'top_level_build'
                self.db.upsert(id, r)
            elif r.has_key('unit'):
                r['type'] = 'distro_level_build'
                self.db.upsert(id, r)
            elif r.has_key('committer'):
                r['type'] = 'commit'
                self.db.upsert(id, r)
            elif r.has_key('tests'):
                r['type'] = 'test_run'
                self.db.upsert(id, r)
            else:
                print 'unknown type : ', 
                print r
