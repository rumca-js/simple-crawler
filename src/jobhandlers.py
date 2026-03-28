import subprocess
import time

from webtoolkit import (
   BaseUrl,
   RemoteUrl,
   UrlLocation,
   PageRequestObject,
   ContentLinkParser,
   HTTP_STATUS_CODE_SERVER_TOO_MANY_REQUESTS,
   HTTP_STATUS_TOO_MANY_REQUESTS,
)

from .controller import Controller
from .sources import Sources
from .entries import Entries
from .sourcedata import SourceData
from .applogging import AppLogging
from .entryrules import EntryRules
from .entryurlinterface import EntryUrlInterface
from .controller import Controller
from .urlhandler import UrlHandler


class GenericJobHandler(object):
    def __init__(self, connection, job, table_name):
        self.connection = connection
        self.job = job
        self.table_name = table_name

    def close(self):
        self.connection.backgroundjob.delete(id=self.job.id)

    def run(self):
        pass


class ProcessSourceJobHandler(GenericJobHandler):

    def run(self):
        source_id = int(self.job.subject)
        sources = Sources(self.connection)
        source = sources.get(id=source_id)
        if source is not None:
            self.check_source(source)
        # source might have been removed

    def check_source(self, source):
        url = self.get_response_real(source)

        if not url:
            return

        response = url.get_response()
        if response is not None:
            if response.is_valid():
                self.handle_valid_response(source, url, response)

                sourcedata = SourceData(self.connection)
                sourcedata.mark_read(source)
            else:
                AppLogging(self.connection).error(f"URL:{source.url} Response is invalid")
        else:
            AppLogging(self.connection).error(f"URL:{source.url} No response")

    def get_response_real(self, source):
        while True:
            url = self.get_source_url(source)
            if not url:
                return

            response = url.get_response()
            if response:
                if (response.get_status_code() == HTTP_STATUS_TOO_MANY_REQUESTS or
                    response.get_status_code() == HTTP_STATUS_CODE_SERVER_TOO_MANY_REQUESTS):
                    AppLogging(self.connection).debug("Retry of request")
                    continue
            if response is None:
                AppLogging(self.connection).error(f"URL:{source.url} No response")
                return

            return url

    def handle_valid_response(self, source, url, response):
        source_properties = url.get_properties()

        sources = Sources(self.connection)
        sources.set(source.url, source_properties)
        #sources.delete_entries(source)

        links = self.get_links(url)
        entries = Entries(self.connection)

        for link in links:
            exists = self.connection.entries_table.exists(link=link)
            if not exists and UrlLocation(link).is_webpage_link():
                self.process_link(link, source)

    def on_added_entry(self, entry):
        rules = EntryRules(self.connection).get_rules_for(entry=entry)
        for rule in rules:
            if not rule.enabled:
                continue

            """
            if rule.script:
                subprocess.run(rule.script, shell=True, capture_output=True, text=True)
            """

    def is_entry_ok(self, entry, source):
        if entry is None:
            return False

        link = entry.get("link")
        if not link:
            return False

        if source.xpath:
            try:
                if re.search(source.xpath, link) is None:
                    return False
            except re.error as E:
                AppLogging(self.connection).exc(E, "Incorrect pattern")
                return False

        return True

    def on_done(self, response):
        pass

    def process_source(self, index, source_id, source_count):
        sources = Sources(self.connection)
        source = sources.get(id=source_id)

        if not source:
            AppLogging(self.connection).debug(f"Source id: {source_id} Could not find source")
            return False

        if not source.enabled:
            AppLogging(self.connection).debug(f"Source id: {source_id} Source is not enabled")
            return False

        rules = EntryRules(self.connection)
        if rules.is_entry_rule_triggered(source.url):
            sources = Sources(connection=self.connection)
            sources.delete(id=source.id)
            return False

        sources_data = SourceData(self.connection)

        if not sources_data.is_update_needed(source):
            now = datetime.now()
            AppLogging(self.connection).debug(f"{source.url}: Update not needed @ {now}")
            return False

        AppLogging(self.connection).debug(f"{index}/{source_count} {source.url} {source.title}: Reading")
        self.check_source(source)

        #writer = SourceWriter(connection=self.connection, source=source)
        #writer.write()

        AppLogging(self.connection).debug(f"{index}/{source_count} {source.url} {source.title}: Reading DONE")
        time.sleep(1)

        return True

    def process_link(self, link, source):
        entry_json = self.link_to_entry(link, source)
        if self.is_entry_ok(entry_json, source):
            entries = Entries(self.connection)
            entry_id = entries.add(entry_json, source)
            entry = entries.get(id=entry_id)

            controller = Controller(self.connection)
            controller.add_social_data(entry)

    def get_links(self, url):
        response = url.get_response()
        if response:
            text = response.get_text()

            parser = ContentLinkParser(url.url, text)
            return parser.get_links()
        return []

    def link_to_entry(self, link, source):
        handler = UrlHandler(connection=self.connection, link=link)
        url = handler.get_link_url()
        url.get_response()

        entry_interface = EntryUrlInterface(url=url, source=source)
        entry = entry_interface.get_entry_json()

        return entry

    def get_source_url(self, source):
        handler = UrlHandler(connection=self.connection, link=source.url)
        url = handler.get_link_url()
        if not url:
            AppLogging(self.connection).notify(f"Removing invalid source:{source.url}")
            sources = Sources(self.connection)
            sources.delete(id=source.id)
        return url


class UpdateLinkJobHandler(GenericJobHandler):
    def run(self):
        entry_id = int(self.job.subject)
        entries = Entries(self.connection)
        entry = entries.get(id=entry_id)
        self.update_entry(entry)

    def update_entry(self, entry):
        handler = UrlHandler(connection=self.connection, link=entry.link)
        url = handler.get_link_url()

        json_data = {}
        json_data["date_updated"] = datetime.now()

        #if not entry.title:
        #    entry.title = url.get_title()
        #if not entry.description:
        #    entry.description = url.get_description()
        ##TODO implement rest

        controller = Controller(self.connection)
        controller.add_social_data(entry)

        self.connection.entries_table.update_json_data(id=entry.id, json_data=json_data)


class ResetLinkJobHandler(GenericJobHandler):
    def run(self):
        entry_id = int(self.job.subject)
        entries = Entries(self.connection)
        entry = entries.get(id=entry_id)
        self.reset_entry(entry)

    def reset_entry(self, entry):
        handler = UrlHandler(connection=self.connection, link=entry.link)
        url = handler.get_link_url()

        json_data = {}
        json_data["date_updated"] = datetime.now()

        #if url.get_title():
        #    entry.title = url.get_title()
        #if url.get_description():
        #    entry.description = url.get_description()
        ##TODO implement rest

        controller = Controller(self.connection)
        controller.add_social_data(entry)

        self.connection.entries_table.update_json_data(id=entry.id, json_data=json_data)


class CleanupJobHandler(GenericJobHandler):
    def run(self):
        self.add_due_sources()

        entries = Entries(self.connection)
        entries.cleanup()
        sources_data = SourceData(self.connection)
        sources_data.cleanup()

    def add_due_sources(self):
        status = False

        self.controller = Controller(connection=self.connection)

        sources = self.controller.get_sources_to_add()
        if sources:
            self.start_reading = True
            self.controller.add_sources(sources)
            status = True

        return status
