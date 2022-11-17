import feedparser
import threading
import pandas as pd
import sqlite3
import urllib.parse

from flask import Flask, render_template, request, make_response
from html.parser import HTMLParser
from io import StringIO
from transformers import pipeline

class MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs= True
        self.text = StringIO()
    def handle_data(self, d):
        self.text.write(d)
    def get_data(self):
        return self.text.getvalue()

class EndpointHandler(object):
    def __init__(self, action):
        self.action = action

    def __call__(self):
        data = {}
        if len(request.view_args) > 0:
            print(f'Args: {request.view_args}')
            data = request.view_args
        else:
            str = request.data.decode("utf-8")
            str = urllib.parse.unquote(str)
            if len(str) > 0:
                arr = str.split('=')
                data[arr[0]] = arr[1]
        response = self.action(**data)
        return make_response(response)

class FlaskApp(object):
    summarizer = None
    # arXiv category to fetch
    category = 'cs.CV'

    def __init__(self, app, **configs):
        self.app = app
        self.configs(**configs)

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

    def add_endpoint(self, rule: str, endpoint=None, handler=None, methods=['GET'], *args, **kwargs):
        self.app.add_url_rule(rule, endpoint, EndpointHandler(handler), methods=methods, *args, **kwargs)

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def index(self):
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT id, created, title, link, author, brief FROM papers WHERE NOT hide ORDER BY created DESC")
        df = pd.DataFrame(c.fetchall(), columns=['id', 'created', 'title','link', 'author', 'brief'])
        conn.close()
        df['id'] = [i.replace('http://arxiv.org/abs/', '') for i in list(df['id'])]
        return render_template('index.html', data=df.to_dict('records'))

    def fetch(self):
        thread = threading.Thread(target=self.get_feed, name="Get RSS Feed")
        thread.start()
        thread.join()
        return self.alert

    def hide(self, **kwargs):
        self.data = kwargs['items']
        thread = threading.Thread(target=self.hide_items, name="Hide papers")
        thread.start()
        thread.join()
        return self.alert

    def strip_tags(self, html):
        s = MLStripper()
        s.feed(html)
        return s.get_data()

    def get_feed(self):
        d = feedparser.parse(f'http://arxiv.org/rss/{self.category}')
        # Get DB connection
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        added = []
        skipped = 0
        # Iterate over results one-by-one
        for p in d.entries:
            pid = p.id
            sql = f"SELECT COUNT(*) FROM papers WHERE id = '{pid}'"
            res = c.execute(sql)
            # print(f'Checking: {sql}')
            cnt = res.fetchone()[0]
            pid = pid.replace('http://arxiv.org/abs/', '')
            if cnt > 0:
                # We found a match, but records are not in order, so need to loop through all records
                # print(f'Match found. Skipping {pid}')
                skipped +=1
                continue
            # If we got here, there was no match - must add record to DB. Clean data first
            author = self.strip_tags(p.author)
            summary = self.strip_tags(p.summary)
            # Set up summarizer, if necessary
            if self.summarizer is None:
                self.summarizer = pipeline("summarization", "pszemraj/long-t5-tglobal-base-16384-book-summary")
                self.tokenizer = self.summarizer.tokenizer
            # Get token count, to configure summarizer
            toks = self.tokenizer.tokenize(summary)
            cnt = len(toks)
            max = min(512, int(cnt / 2))
            # Get summary
            brief = self.summarizer(summary, max_length=max)[0]['summary_text']
            # Set up SQL
            sql = 'INSERT INTO papers(id, title, category, link, summary, author, brief, created) VALUES (' \
                f'?, ?, "{self.category}", ?, ?, ?, ?, datetime("now"))'
            # Add record
            try:
                print(f'Adding record for: {pid}')
                c.execute(sql, (p.id, p.title, p.link, summary, author, brief))
                conn.commit()
                added.append(pid)
            except:
                print(f'Error saving data: {sql}')
        # Close DB connection
        count = len(added)
        print(f'Added: {count} and skipped: {skipped}  papers.')
        conn.close()
        # Update view with message - returned from fetch()
        if count == 0:
            self.alert = 'Did not fetch any new papers.'
        else:
            self.alert = f'Added {count} papers to the list: ' + ', '.join(added) + '. Refresh to load them.'

    def hide_items(self):
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        sql = f'UPDATE papers SET hide = 1 WHERE id IN ({self.data})'
        c.execute(sql)
        conn.commit()
        cnt = c.rowcount
        conn.close()
        self.data = ''
        self.alert = f'Hid {cnt} records. Refresh to update data.'

flask = Flask(__name__)
app = FlaskApp(flask)
# Add endpoints for the action function
app.add_endpoint('/', 'index',  app.index, methods=['GET'])
app.add_endpoint('/fetch', 'fetch', app.fetch, methods=['POST'])
app.add_endpoint('/hide', 'hide', app.hide, methods=['POST'])
# app.add_endpoint('/drop', 'drop', app.drop, methods=['POST'])

if __name__ == "__main__":
    app.run()