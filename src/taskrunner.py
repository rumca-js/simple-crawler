import re
import time
import traceback
from datetime import datetime, timedelta
from webtoolkit import (
   BaseUrl,
   RemoteUrl,
   PageRequestObject,
   ContentLinkParser,
   UrlLocation,
)

from .dbconnection import DbConnection
from .controller import Controller
from .system import System
from .sourcedata import SourceData
from .socialdata import SocialData
from .sources import Sources
from .entries import Entries
from .applogging import AppLogging


class TaskRunner(object):
    def __init__(self, table_name):
        self.connection = None
        self.controller = None
        self.table_name = table_name

        system = System.get_object()
        system.set_thread_ok()

        self.waiting_due = None
        self.start_reading = True

    def check_source(self, source):
        print("Check source")
        sourcedata = SourceData(self.connection)
        sourcedata.mark_read(source)

        url = self.get_source_url(source)
        if not url:
            print("No source url")
            return

        response = url.get_response()
        if response:
            print("Check source - reponse")
            if response.is_valid():
                print("Check source - reponse valid")
                source_properties = url.get_properties()

                sources = Sources(self.connection)
                sources.set(source.url, source_properties)
                #sources.delete_entries(source)

                # add entry for source
                # self.process_link(source.url, source)

                links = self.get_links(url)
                print(f"Found links {links}")
                for link in links:
                    if UrlLocation(link).is_webpage_link():
                        self.process_link(link, source)
            else:
                AppLogging(self.connection).error(f"URL:{source.url} Response is invalid")
                time.sleep(5)
        else:
            AppLogging(self.connection).error(f"URL:{source.url} No response")
            time.sleep(5)

    def process_link(self, link, source):
        print("process_link")
        entry = self.link_to_entry(link, source)
        if self.is_entry_ok(entry, source):
            entries = Entries(self.connection)
            entry_id = entries.add(entry, source)

            social_data_info = self.link_to_social_data(link)
            if social_data_info:
                social_data = SocialData(self.connection)
                social_data.add(entry_id, social_data_info)

    def link_to_social_data(self, link):
        url = self.get_link_url(link)
        social_data_info = url.get_social_properties()
        return social_data_info

    def get_links(self, url):
        response = url.get_response()
        if response:
            text = response.get_text()

            parser = ContentLinkParser(url.url, text)
            return parser.get_links()
        return []

    def link_to_entry(self, link, source):
        url = self.get_link_url(link)
        url.get_response()

        entry = {}
        entry["link"] = link
        entry["title"] = url.get_title()
        entry["description"] = url.get_description()
        entry["status_code"] = url.get_status_code()
        if source:
            entry["source_id"] = source.id

        return entry

    def is_entry_ok(self, entry, source):
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

    def get_source_url(self, source):
        url = self.get_link_url(source.url)
        if not url:
            AppLogging(self.connection).notify(f"Removing invalid source:{source.url}")
            sources = Sources(self.connection)
            sources.delete(id=source.id)
        return url

    def get_link_url(self, link):
        request = PageRequestObject(link)
        request.timeout_s = 300

        config = self.connection.configurationentry.get()
        try:
            if self.is_remote_server() or self.is_config_remote_server():
                # TODO dates are strings
                location = config.remote_webtools_server_location
                if not location:
                    location = RemoteUrl.get_remote_server_location()

                url = RemoteUrl(request=request, remote_server_location=location)
            else:
                url = BaseUrl(request=request)
            return url
        except:
            AppLogging(self.connection).notify(f"Cannot obtain data for:{link}")
    
    def is_remote_server(self):
        return RemoteUrl.get_remote_server_location()

    def is_config_remote_server(self):
        config = self.connection.configurationentry.get()
        if config.remote_webtools_server_location is None:
            return False
        if config.remote_webtools_server_location == "":
            return False
        if config.remote_webtools_server_location == "None":
            return False
        return True

    def on_done(self, response):
        pass

    def start(self, init_sources=None):
        """
        Called from a thread
        """
        try:
            self.connection = DbConnection(self.table_name)
            self.controller = Controller(connection=self.connection)

            self.sources_data = SourceData(self.connection)

            self.setup_start()

            sources = Sources(self.connection)
            sources_len = sources.count()

            if sources_len == 0:
                self.init_sources()

            self.controller.close()
            self.connection.close()

            self.process_sources()
        except Exception as e:
            traceback.print_exc()

    def init_sources(self):
        self.controller.read_sources()

    def setup_start(self):
        entries = Entries(self.connection)
        entries_len = entries.count()
        sources = Sources(self.connection)
        sources_len = sources.count()

        print(f"Entries: {entries_len}")
        print(f"Sources: {sources_len}")

    def process_sources(self):
        print("Starting reading")
        while True:
            try:
                system = System.get_object()

                self.start_reading = False

                source_ids = self.get_sources_ids()

                no_source_read = True
                for index, source_id in enumerate(source_ids):
                    self.connection = DbConnection(self.table_name)
                    self.controller = Controller(connection=self.connection)

                    if self.process_source(index, source_id, len(source_ids)):
                        no_source_read = False

                    self.controller.close()
                    self.connection.close()
                    system.set_thread_ok()

                system.set_thread_ok()

                if no_source_read:
                    self.waiting_due = datetime.now() + self.get_due_time()
                    self.wait_for_due_time()

                self.connection = DbConnection(self.table_name)
                self.controller = Controller(connection=self.connection)

                entries = Entries(self.connection)
                entries.cleanup()
                sources_data = SourceData(self.connection)
                sources_data.cleanup()

                self.controller.close()
                self.connection.close()
            except Exception as E:
                AppLogging(self.connection).exc(E, "Error during processing of source")
                time.sleep(1)

    def get_due_time(self):
        return timedelta(minutes = 10)

    def wait_for_due_time(self):
        system = System.get_object()
        while True:
            system.set_thread_ok()

            if self.start_reading:
                return True

            if datetime.now() < self.waiting_due:
                time.sleep(10)
            else:
                self.start_reading = True
                return True

    def get_sources_ids(self):
        """
        we assume that we have a small number of sources
        """
        self.connection = DbConnection(self.table_name)
        self.controller = Controller(connection=self.connection)

        sources = Sources(self.connection)
        source_count = sources.count()

        source_ids = []
        for source in self.connection.sources_table.get_sources():
            source_ids.append(source.id)

        self.controller.close()
        self.connection.close()

        return source_ids

    def process_source(self, index, source_id, source_count):
        sources = Sources(self.connection)
        source = sources.get(id=source_id)

        if not source:
            AppLogging(self.connection).debug(f"Source id: {source_id} Could not find source")
            return False

        if not source.enabled:
            AppLogging(self.connection).debug(f"Source id: {source_id} Source is not enabled")
            return False

        if self.controller.is_entry_rule_triggered(source.url):
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

        AppLogging(self.connection).debug(f"{index}/{source_count} {source.url} {source.title}: Reading DONE")
        time.sleep(1)

        return True
