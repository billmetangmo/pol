import w3lib.url
import w3lib.html

from lxml import etree
import re, sys
from hashlib import md5

from feedgenerator import Rss201rev2Feed, Enclosure
import datetime

import MySQLdb
from settings import DATABASES, DOWNLOADER_USER_AGENT

url_hash_regexp = re.compile('(#.*)?$')

POST_TIME_DISTANCE = 15 # minutes, RSS Feed Reader skip same titles created in 10 min interval

FIELD_IDS = {'title': 1, 'description': 2, 'link': 3}

def save_post(conn, created, feed_id, post_fields):
    cur = conn.cursor()
    cur.execute("""insert into frontend_post (md5sum, created, feed_id)
                    values (%s, %s, %s)""", (post_fields['md5'], created, feed_id))
    print(cur._last_executed)

    post_id = conn.insert_id()
    for key in ['title', 'description', 'title_link']:
        if key in post_fields:
            cur.execute("""insert into frontend_postfield (field_id, post_id, `text`)
                            values (%s, %s, %s)""", (FIELD_IDS[key], post_id, post_fields[key].encode('utf-8')))
            print(cur._last_executed)

def fill_time(feed_id, items):
    if not items:
        return []
    for item in items:
        #create md5
        h = md5('')
        for key in ['title', 'description', 'link']:
            if key in item:
                h.update(item[key].encode('utf-8')) 
        item['md5'] = h.hexdigest()

    #fetch dates from db
    fetched_dates = {}
    db = get_conn()
    with db:
        quoted_hashes = ','.join(["'%s'" % (i['md5']) for i in items])

        cur = db.cursor()
        cur.execute("""select p.md5sum, p.created, p.id
                       from frontend_post p
                       where p.md5sum in (%s)
                       and p.feed_id=%s""" % (quoted_hashes, feed_id,))
        rows = cur.fetchall()
        print(cur._last_executed)
        for row in rows:
            md5hash = row[0]
            created = row[1]
            post_id = row[2]
            fetched_dates[md5hash] = created
    cur_time = datetime.datetime.utcnow()
    new_posts = []
    for item in items:
        if item['md5'] in fetched_dates:
            item['time'] = fetched_dates[item['md5']]
        else:
            item['time'] = cur_time
            save_post(db, cur_time, feed_id, item)
            cur_time -= datetime.timedelta(minutes=POST_TIME_DISTANCE)


def decode(text, encoding): # it's strange but true
    if isinstance(text, unicode):
        return text
    else:
        return text.decode(encoding)

def element_to_unicode(element, encoding):
    if isinstance(element, basestring): # attribute
        return decode(element, encoding)

    s = [decode(element.text, encoding)] if element.text else []
    for sub_element in element:
        s.append(decode(etree.tostring(sub_element), encoding))
    return u''.join(s)

def _build_link(html, doc_url, url):
    base_url = w3lib.html.get_base_url(html, doc_url)
    return w3lib.url.urljoin_rfc(base_url, url).decode('utf-8')

def buildFeed(response, feed_config):
    response.selector.remove_namespaces()

    tree = response.selector._root.getroottree()
    # get data from html 
    items = []
    for node in tree.xpath(feed_config['xpath']):
        item = {}
        required_count = 0
        required_found = 0
        for field_name in ['title', 'description', 'link']:
            if field_name in feed_config['fields']:
                if feed_config['required'][field_name]:
                    required_count += 1
                element_or_attr = node.xpath(feed_config['fields'][field_name])
                if element_or_attr:
                    item[field_name] = element_to_unicode(element_or_attr[0], response.encoding)
                    if feed_config['required'][field_name]:
                        required_found += 1
                    if field_name == 'link':
                        item['link'] = _build_link(response.body_as_unicode(), feed_config['uri'], item['link'])

        if required_count == required_found:
            items.append(item)

    title = response.selector.xpath('//title/text()').extract()

    #build feed
    feed = Rss201rev2Feed(
        title = title[0] if title else 'Polite Pol: ' + feed_config['uri'],
        link=feed_config['uri'],
        description="Generated by PolitePol.com.\n"+\
            "Source page url: " + feed_config['uri'],
        language="en",
    )

    fill_time(feed_config['id'], items)

    for item in items:
        title = item['title'] if 'title' in item else ''
        desc = item['description'] if 'description' in item else ''
        time = item['time'] if 'time' in item else datetime.datetime.utcnow()
        if 'link' in item:
            link = item['link']
        else:
            link = url_hash_regexp.sub('#' + md5((title+desc).encode('utf-8')).hexdigest(), feed_config['uri'])
        feed.add_item(
            title = title,
            link = link,
            unique_id = link,
            description = desc,
            #enclosure=Enclosure(fields[4], "32000", "image/jpeg") if  4 in fields else None, #"Image"
            pubdate = time
        )
    return feed.writeString('utf-8')

def getFeedData(request, feed_id):
    # get url, xpathes
    feed = {}
    db = get_conn()
    with db:
        cur = db.cursor()
        cur.execute("""select f.uri, f.xpath, fi.name, ff.xpath, fi.required from frontend_feed f
                       right join frontend_feedfield ff on ff.feed_id=f.id
                       left join frontend_field fi on fi.id=ff.field_id
                       where f.id=%s""", (feed_id,))
        rows = cur.fetchall()

        for row in rows:
            if not feed:
                feed['id'] = feed_id
                feed['uri'] = row[0]
                feed['xpath'] = row[1]
                feed['fields'] = {}
                feed['required'] = {}
            feed['fields'][row[2]] = row[3]
            feed['required'][row[2]] = row[4]

    if feed:
        return [feed['uri'], feed]
    else:
        return 'Feed generator error: config of feed is empty'

def get_conn():
    creds = DATABASES['default']
    db = MySQLdb.connect(host=creds['HOST'], port=int(creds['PORT']), user=creds['USER'], passwd=creds['PASSWORD'], db=creds['NAME'], init_command='SET NAMES UTF8')
    db.autocommit(True)
    return db
