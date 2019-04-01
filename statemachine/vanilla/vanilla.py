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
                         'missing_resource',
                         'install',
                         'installed',
                         'blocked',
                         'waiting',
                         'ready',
                         'configured',
                         'started')

    transitions = {
        states.init: (states.missing_resource, states.install),
        states.install: states.installed,
        states.installed: (states.blocked, states.waiting),
        (states.blocked, states.waiting): states.ready,
        states.ready: (states.blocked, states.waiting, states.configured),
        states.configured: (states.blocked, states.waiting, states.started),
    }

    reactions = {
        resources.vanilla.states.failed: states.missing_resource,
        resources.vanilla.states.fetched: states.install,
        states.all(states.installed,
                   apache_php.states.installed,
                   states.not_all(mysql.states.joined, pgsql.states.joined),
                   ): states.blocked,
        states.all(states.any(states.installed, states.blocked),
                   apache_php.states.installed,
                   states.not_all(mysql.states.ready, pgsql.states.ready),
                   ): states.waiting,
        states.all(states.any(states.waiting, states.blocked),
                   states.any(mysql.states.ready, pgsql.states.ready)
                   ): states.ready,
        apache_php.states.started: states.started,
    }

    @property
    def db(self):
        if pgsql.joined:  # prefer pgsql over mysql
            return pgsql
        elif mysql.joined:
            return mysql
        else:
            return None

    def handle_init(self):
        unit.status.maintenance('fetching resources')
        resources.vanilla.fetch()

    def handle_missing_resource(self):
        unit.status.blocked('missing resource: vanilla')

    def handle_install(self):
        unit.status.maintenance('unpacking resources')
        shutil.unpack_archive(resources.vanilla.filename,
                              '/var/www/vanilla')
        apache_php.add_site('/var/www/vanilla',
                            options='Indexes FollowSymLinks MultiViews')
        return self.states.installed

    def handle_blocked(self):
        unit.status.blocked('missing relation to one of: mysql or pgsql')
        if unit.is_leader:
            app.status.blocked('missing relation to one of: mysql or pgsql')

    def handle_waiting(self):
        unit.status.waiting(f'waiting on {self.db.endpoint_name}')
        if unit.is_leader:
            app.status.waiting(f'waiting on {self.db.endpoint_name}')

    def handle_ready(self):
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
        return self.states.configured

    def handle_started(self):
        unit.status.active('started')
        if unit.is_leader:
            app.status.active('started')
