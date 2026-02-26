from pathlib import Path
from datetime import datetime
from .sourcedata import SourceData
from .sources import Sources


def read_line_things(input_text):
    sources = [
        line.strip()
        for line in input_text.splitlines()
        if line.strip()
    ]

    sources = set(sources)
    sources = list(sources)

    return sources


class Controller(object):
    def __init__(self, connection):
        self.connection = connection

    def read_sources(self):
        self.connection.source_table.count() == 0:
            path = Path("config") / "seed.txt"
            if path.exists():
                text = path.read_text()
                lines = read_line_things(text)
                for line in lines:
                    self.set_source(line)

    def set_source(self, link):
        sources = Sources(self.connection)
        sources.set(link)

    def is_entry_rule_triggered(self, url) -> bool:
        rules = self.connection.entry_rules.get_where({"trigger_rule_url" : url})
        rules = next(rules, None)
        if rules:
            return True
        return False

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
