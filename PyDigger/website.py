from flask import Flask, render_template, redirect, request, url_for, Response, jsonify, g
from datetime import datetime
import hashlib
import json
import logging
import logging.handlers
import math
import os
import pymongo
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PyDigger.common
from PyDigger.common import cases, get_stats_from_cache, get_latests


max_license_length = 50

app = Flask(__name__)

@app.before_request
def before_request():
    g.request_start_time = time.time()
    g.request_time = lambda: "%.5fs" % (time.time() - g.request_start_time)


def setup():
    # set up logging
    log_level = logging.ERROR
    if os.environ.get('PYDIGGER_TEST'):
        log_level = logging.DEBUG

    root = PyDigger.common.get_root()
    logdir = root + '/log'
    if not os.path.exists(logdir):
        os.mkdir(logdir)
    log_file = logdir + '/app.log'
    log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)-10s - %(message)s')
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=10)
    handler.setLevel(log_level)
    handler.setFormatter(log_format)
    app.logger.addHandler(handler)

    app.logger.setLevel(log_level)

    app.logger.info("setup")


if not os.environ.get('PYDIGGER_SKIP_SETUP'):
    setup()


@app.template_filter()
def commafy(value):
    return '{:,}'.format(value)


def gravatar(email):
    if email is None:
        return ''
    return hashlib.md5(email.strip().lower().encode('utf8')).hexdigest()


def get_int(field, default):
    value = request.args.get(field, default)
    try:
        value = int(value)
    except Exception:
        value = default
    return value


@app.route("/api/0/recent")
def api_recent():
    query = {}
    skip = 0
    limit = 20
    db = PyDigger.common.get_db()
    data = db.packages.find(query).sort([("upload_time", pymongo.DESCENDING)]).skip(skip).limit(limit)
    my = []
    for entry in data:
        my.append({
            'home_page': entry.get('home_page'),
            'name': entry['name'],
        })
    app.logger.info("api_recent")
    app.logger.info(my)
    #return "OK"
    return jsonify(my)


@app.route("/keyword/<keyword>")
def keyword(keyword):
    app.logger.info(f"/keyword/{keyword}")
    return show_list(keyword = keyword)


@app.route("/author/<name>")
def author(name):
    app.logger.info(f"/author/{name}")
    return show_list(name = name)


@app.route("/search/<word>")
def search(word):
    app.logger.info(f"/search/{word}")
    return show_list(word = word)

@app.route("/search")
def search_none():
    app.logger.info("/search")
    return show_list()

@app.route("/")
def main():
    app.logger.info("/")
    return show_list()

def show_list(word = '', keyword = '', name = ''):
    latest = get_latests()

    db = PyDigger.common.get_db()
    total_indexed = db.packages.count_documents({})
    limit = get_int('limit', 20)
    page = get_int('page', 1)
    mongo_query = {}
    search_query = request.args.get('q', '').strip()
    license = request.args.get('license', '').strip()
    if limit == 0:
        limit = 20

    word = word.replace('-', '_')
    if (word in cases):
        mongo_query = cases[word]
        search_query = ''

    if keyword:
        mongo_query = { 'split_keywords' : keyword }
        search_query = ''

    if name != '':
        mongo_query = {'author': name}
        search_query = ''

    if search_query != '':
        mongo_query = {'$or' : [ {'name' : { '$regex' : search_query, '$options' : 'i'}}, { 'split_keywords' : search_query.lower() } ] }

    if license != '':
        if license == '__long__':
            this_regex = '.{' + str(max_license_length) + '}'
            mongo_query = {'$and' : [ {'license': {'$exists': True} }, { 'license' : { '$regex': this_regex } }] }
        elif license == '__empty__':
            mongo_query = {'$and' : [ {'license': {'$exists': True} }, { 'license' : '' }] }
        else:
            mongo_query = {'license' : license}
        if license == 'None':
            mongo_query = {'license' : None}

    skip = max(limit * (page - 1), 0)
    data = db.packages.find(mongo_query).sort([("upload_time", pymongo.DESCENDING)]).skip(skip).limit(limit)
#    total_found = db.packages.find(mongo_query).count()
    total_found = db.packages.count_documents(mongo_query)
    count = db.packages.count_documents(mongo_query, limit=limit)

    gravatar_code = None
    if name and total_found > 0:
        try:
            gravatar_code = gravatar(data[0].get('author_email'))
            app.logger.info(f"The gravatar_code={gravatar_code}")
        except Exception as err:
            app.logger.error(f"Could not get gravatar_code {err}")

    return render_template('main.html',
        title = "PyDigger - unearthing stuff about Python",
        page = {
            'total_indexed' : total_indexed,
            'total_found' : total_found,
            'count' : count,
            'pages' : int(math.ceil(total_found / limit)),
            'current' : page,
            'limit' : limit,
        },
        latest = latest,
        data = data,
        search_query = search_query,
        author = name,
        gravatar = gravatar_code,
    )

@app.route("/keywords")
def keywords():
    app.logger.info("/keywords")
    db = PyDigger.common.get_db()
    packages = db.packages.find({'$and' : [{'split_keywords' : { '$exists' : True }}, { 'split_keywords': {'$not' : { '$size' : 0}}}] }, {'split_keywords': True})
    # TODO: tshis should be really improved
    keywords = {}
    total = 0
    for p in packages:
        for k in p['split_keywords']:
            if k not in keywords:
                keywords[k] = 0
            keywords[k] += 1
            total += 1
    words = [ (k, keywords[k]) for k in keywords.keys() ]
    words.sort(key=lambda f:f[1])
    words.reverse()

    return render_template('keywords.html',
        title = "Keywords of Python packages on PyPI",
        words = words,
        total = total,
        unique = len(words),
        stats = get_stats_from_cache(),
    )

@app.route("/licenses")
def licenses():
    db = PyDigger.common.get_db()
    licenses = db.packages.group(['license'], {}, { 'count' : 0}, 'function (curr, result) { result.count++; }' )
    licenses.sort(key=lambda f:f['count'])
    licenses.reverse()
    for licence in licenses:
        licence['count'] = int(licence['count'])
        if licence['license'] is None:
            licence['license'] = 'None'
        if len(licence['license']) > max_license_length:
            licence['long'] = True

    return render_template('licenses.html',
        title = "Licenses of Python packages on PyPI",
        total = db.packages.find().count(),
        has_license = db.packages.find(cases['has_license']).count(),
        no_license = db.packages.find(cases['no_license']).count(),
        licenses = licenses,
    )

@app.route("/stats")
def stats():
    app.logger.info("/stats")
    stats = get_stats_from_cache()

    return render_template('stats.html',
        title = "PyDigger - Statistics",
        stats = stats,
    )

@app.route("/pypi/<name>")
def pypi(name):
    db = PyDigger.common.get_db()
    app.logger.info(f"/pypi/{name}")
    package = db.packages.find_one({'lcname' : name.lower()})
    if not package:
        return render_template('404.html',
            title = name + " not found",
            package_name = name), 404

    if package['name'] != name:
        return redirect(url_for('pypi', name = package['name']))

    # if 'keywords' in package and package['keywords']:
    #     package['keywords'] = package['keywords'].split(' ')
    # else:
    #     package['keywords'] = []

    return render_template('package.html',
        title = name,
        package = package,
        gravatar = gravatar(package.get('author_email')),
        raw = json.dumps(package, indent=4, default = json_converter)
    )

@app.route("/robots.txt")
def robots():
    #robots = '''Sitemap: http://pydigger.com/sitemap.xml
    robots = '''Disallow: /static/*
'''
    return Response(robots, mimetype='text/plain')

# @app.route("/sitemap.xml")
# def sitemap():
#     xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
#     xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
#     today = datetime.now().strftime("%Y-%m-%d")
#
#     for page in ('', 'stats', 'about'):
#         xml += '  <url>\n'
#         xml += '    <loc>http://pydigger.com/{}</loc>\n'.format(page)
#         xml += '    <lastmod>{}</lastmod>\n'.format(today)
#         xml += '  </url>\n'
#
#     # fetch all
#     xml += '</urlset>\n'
#     return Response(xml, mimetype='aplication/xml')


@app.route("/about")
def about():
    return render_template('about.html',
        title = "About PyDigger"
    )

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

def json_converter(o):
    if isinstance(o, datetime):
        return o.__str__()
