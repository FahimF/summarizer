import feedparser
import threading
import pandas as pd
import sqlite3
import torch

from enum import Enum
from flask import Flask, render_template
from html.parser import HTMLParser
from io import StringIO
from transformers import pipeline

class AlertType(Enum):
    DANGER = 1
    INFO = 2
    SUCCESS = 3
    WARNING = 4

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

class FlaskApp(object):
    summarizer = None

    def __init__(self, app, **configs):
        self.app = app
        self.configs(**configs)
        # self.device = torch.device('cuda' if torch.cuda.is_available else 'mps' if torch.has_mps else 'cpu')

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

    def add_endpoint(self, endpoint=None, endpoint_name=None, handler=None, methods=['GET'], *args, **kwargs):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods=methods, *args, **kwargs)

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def index(self, alert: str = '', alert_type: AlertType = None):
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT title, link, author, brief FROM papers")
        df = pd.DataFrame(c.fetchall(), columns=['title','link', 'author', 'brief'])
        conn.close()
        msg = ('', 0) if len(alert) == 0 else (alert, alert_type.name)
        return render_template('index.html', data=df.to_dict('records'), alert=msg)

    def fetch(self):
        thread = threading.Thread(target=self.get_feed, name="Get RSS Feed")
        thread.start()
        return self.index(alert='Fetch started ...', alert_type=AlertType.INFO)

    def strip_tags(self, html):
        s = MLStripper()
        s.feed(html)
        return s.get_data()

    def get_feed(self):
        d = feedparser.parse(r'http://arxiv.org/rss/cs.CV')
        # Get DB connection
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        count = 0
        # Iterate over results one-by-one
        for p in d.entries:
            pid = p.id
            res = c.execute("SELECT COUNT(*) FROM papers WHERE id = '{pid}'")
            cnt = res.fetchone()[0]
            if cnt > 0:
                # We found a match, since records are in order, no need to go through rest
                break
            # If we got here, there was no match - must add record to DB. Clean data first
            author = self.strip_tags(p.author)
            summary = self.strip_tags(p.summary)
            # Get summary
            if self.summarizer is None:
                self.summarizer = pipeline("summarization", "pszemraj/long-t5-tglobal-base-16384-book-summary")
            brief = self.summarizer(summary)[0]['summary_text']
            # Set up SQL
            sql = f'INSERT INTO papers(id, title, category, link, summary, author, brief, created) VALUES (' \
                '"{p.id}", "{p.title}", "cs.CV", "{p.link}", "{summary}", "{author}", "{brief}", datetime("now"))'
            # Add record
            try:
                c.execute(sql)
                conn.commit()
                count += 1
            except:
                print(f'Error saving data: {sql}')
        # Close DB connection
        conn.close()
        # Update view with message
        if count == 0:
            msg = 'Did not fetch any new papers.'
        else:
            msg = f'Added {count} papers to the list.'
        return self.index(alert=msg, alert_type=AlertType.SUCCESS)

flask = Flask(__name__)
app = FlaskApp(flask)
# Add endpoints for the action function
app.add_endpoint('/', 'index', app.index, methods=['GET'])
app.add_endpoint('/fetch', 'fetch', app.fetch, methods=['GET'])

if __name__ == "__main__":
    app.run()