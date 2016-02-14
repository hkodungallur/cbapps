# coding=utf-8

#from __future__ import absolute_import, division, print_function
#from future import standard_library; standard_library.install_aliases()

from os.path import join, dirname, realpath
from itertools import groupby
from operator import itemgetter
#from urllib.parse import urlparse
from csv import DictReader
import time
import datetime
import json, sys, os

from requests import get
from flask import Flask, request, render_template, jsonify

from db import buildDB
BLDHISTORY_BUCKET = 'couchbase://cb-bbdb:8091/build-history'
bldDB = buildDB(BLDHISTORY_BUCKET)

with open(join(dirname(__file__), 'VERSION')) as file:
    __version__ = file.read().strip()

app = Flask(__name__)

@app.route('/')
def index():
    projects = []
    rel_lines = bldDB.get_release_lines('watson')
    for line in rel_lines:
        p = {}
        p['release'] = 'watson'
        p['rel_line'] = line
        last_good, recent = bldDB.get_recent_builds('watson', line)
        ks = recent.keys()
        ks.sort(reverse=True)
        p['latest'] = 'Latest Good Build: %s' %(last_good)
        ts = recent[ks[0]]['timestamp']
        updated_at = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ts))
        p['updated_at'] = updated_at
        p['state_class'] = recent[ks[0]]['result']
        if p['state_class'] == 'building':
            p['state_class'] = recent[ks[1]]['result']
        s = []
        for x in ks:
            d = {}
            status = recent[x]['result']
            d['state_label'] = str(x)
            if status == 'success':
                d['state_class'] = 'success'
            elif status == 'building':
                d['state_class'] = 'inprogress'
            else:
                d['state_class'] = 'failure error'

            d['url'] = 'watson/%s' %str(x)
            s.append(d)
        p['statuses'] = s
        projects.append(p)

    return render_template('index.html', projects=projects)

@app.route('/<release>/<rel_line>')
def show_release(release, rel_line):
    if not release in ['watson', 'sherlock']:
        return render_template('404.html')
    history_data = bldDB.get_long_history(release, rel_line)
    history = []
    for h in history_data:
        timestamp = h['timestamp']
        ts = datetime.datetime.fromtimestamp(int(timestamp)/1000).strftime('%Y-%m-%d %H:%M:%S')
        d = {}
        d['project'] = release # make it a top level var instead for each build
        d['b_id'] = h['build_num']
        d['status'] = 'SUCCESS'
        d['timestamp'] = ts
        total_tests, failed_tests = 0, 0
        for distro in h.get('d', []):
            if distro.has_key('testcount'):
                total_tests = total_tests + int(distro['testcount'])
            if distro.has_key('failedtests'):
                failed_tests = failed_tests + int(distro['failedtests'])
            if not distro['result']:
                d['status'] = 'BUILDING'
                break
            if distro['result'] == 'FAILURE':
                d['status'] = 'FAILURE'
                break
        d['total_tests'] = total_tests
        d['total_failed'] = failed_tests
        cmts = []
        for c in h.get('c', []):
            cmts.append({'proj':c['repo'], 'sha':c['sha']})
        d['commits'] = cmts
        history.append(d)

    return render_template('history.html', history=history)

@app.route('/<release>/build/<build_num>')
def specific_build(release, build_num):
    build_data = bldDB.get_build_history(build_num)
    #print json.dumps(build_data[0], indent=2)
    return render_template('onebuild.html', build_data=build_data[0])

@app.route('/<release>/<build_num>/<distro>/<edition>/tests')
def specific_test_run(release, build_num, distro, edition):
    doc_id = '4.5.0-' + build_num + '-' + distro + '-' + edition + '-tests'
    result = bldDB.doc_exists(doc_id)
    test_data = {'build_num': build_num}
    tests = []
    if result:
        tests = parse_test_results(result.value)
    test_data['tests'] = tests
    return render_template('showtests.html', test_data=test_data)

def parse_test_results(test_results):
    tests = test_results['tests']
    allsuites = []
    for t in tests:
        asuite = {}
        asuite['suite_name'] = t['suite']
        asuite['passed'] = []
        asuite['failed'] = []
        asuite['reg'] = []
        for c in t['cases']:
            if c['status'] == 'PASSED':
                asuite['passed'].append(c['name'])
            elif c['status'] == 'FAILED':
                asuite['failed'].append(c['name'])
            elif c['status'] == 'REGRESSION':
                asuite['reg'].append(c['name'])
        allsuites.append(asuite)
    return allsuites
