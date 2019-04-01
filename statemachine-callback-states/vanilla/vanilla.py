import os
import pwd
import shutil

from charms.fsm import CharmStateMachine, CharmStates
from charms import templating

from juju.charm import resources, application as app, unit
from juju.charm.layer import apache_php
from juju.charm.requires import mysql
from juju.charm.requires import pgsql


class VanillaCharm(CharmStateMachine):
    states = CharmStates('init',  # initial state by convention (name or order)
                         'waiting_for_apache',
                         'apache_installed',
                         'blocked',
                         'waiting',
                         'configure',
                         'starting',
                         'started')

    transitions = {
        states.init: states.waiting_for_apache,
        states.waiting_for_apache: states.apache_installed,
        states.apache_installed: (states.blocked,
                                  states.waiting,
                                  states.configure),
        states.blocked: (states.waiting,
                         states.configure),
        states.waiting: (states.blocked,
                         states.configure),
        states.configure: states.starting,
        states.starting: states.started,
        states.started: (states.blocked,
                         states.waiting,
                         states.configure),
    }

    @property
    def db(self):
        if pgsql.joined:  # prefer pgsql over mysql
            return pgsql
        elif mysql.joined:
            return mysql
        else:
            return None

    def _check_db(self):
        # Check DB status and trigger an appropriate state if necessary.
        # Calling CharmState.trigger() will cause the state to transition to
        # the new state at the end of the current handler.  Raising the
        # StateChangedException returned from trigger() will interrupt the
        # current handler and cause the state transition to happen immediately.
        if not self.db:
            raise self.states.blocked.trigger()
        elif not self.db.ready:
            raise self.states.waiting.trigger()
        elif self.db.is_changed:
            raise self.states.configure.trigger()

    def handle_init(self):
        # Handle resources.
        unit.status.maintenance('fetching resources')
        if not resources.vanilla.fetch():
            unit.status.blocked('missing resource: vanilla')
            return
        unit.status.maintenance('unpacking resources')
        shutil.unpack_archive(resources.vanilla.filename,
                              '/var/www/vanilla')
        # Tell apache_php layer to do any installation it needs,
        # and what state to trigger when it's complete.
        unit.status.maintenance('installing apache')
        apache_php.install(ready=self.states.apache_installed)
        # Schedule a transition to the waiting_for_apache state.
        self.states.waiting_for_apache.trigger()

    def handle_apache_installed(self):
        unit.status.maintenance('configuring')
        apache_php.add_site('/var/www/vanilla',
                            options='Indexes FollowSymLinks MultiViews')
        self._check_relations()

    def handle_blocked(self):
        self._check_db()
        unit.status.blocked('missing relation to one of: mysql or pgsql')

    def handle_waiting(self):
        self._check_db()
        unit.status.waiting(f'waiting on {self.db.endpoint_name}')

    def handle_configure(self):
        unit.status.maintenance('configuring webserver')
        templating.render(source='vanilla_config.php',
                          target='/var/www/vanilla/conf/config.php',
                          owner='www-data',
                          perms=0o775,
                          context={
                              'db': self.db,
                          })
        uid = pwd.getpwnam('www-data').pw_uid
        os.chown('/var/www/vanilla/cache', uid, -1)
        os.chown('/var/www/vanilla/uploads', uid, -1)
        # Tell the apache_php layer that we're ready to start,
        # and what state to trigger when it's complete.
        apache_php.start_site('vanilla',
                              started=self.states.started)
        # Schedule a transition to the starting state.
        self.states.starting.trigger()
        unit.status.maintenance('starting')
        # Inform the DB layer that we have handled the changed data.
        self.db.clear_changed()

    def handle_started(self):
        self._check_db()
        unit.status.active('running')
        if unit.is_leader:
            app.status.active('running')
