# I'm sure we could come up with a better syntax / style for this,
# but the basic idea is, as Konstantinos mentioned in the doc, to
# try to separate the flow control (reactions) from implementation.
import os
import pwd
import shutil

from charms.reactive import Flags
from charms import templating

from juju.charm import resources, application as app, unit
from juju.charm.layer import apache_php
from juju.charm.requires import mysql
from juju.charm.requires import pgsql


flags = Flags('vanilla_installed',
              'apache_installed',
              'apache_configured',
              'db_ready',
              'vanilla_configured',
              'vanilla_started')


def get_db():
    if pgsql.joined:  # prefer pgsql over mysql
        return pgsql
    elif mysql.joined:
        return mysql
    else:
        return None


def install(self):
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
    apache_php.install(ready=flags.apache_installed)
    flags.vanilla_installed.set()


def configure_apache(self):
    unit.status.maintenance('configuring apache')
    apache_php.add_site('/var/www/vanilla',
                        options='Indexes FollowSymLinks MultiViews')
    flags.apache_configured.set()


def check_db(self):
    db = get_db()
    if not db:
        unit.status.blocked('missing relation to one of: mysql or pgsql')
        flags.db_ready.clear()
    elif not db.ready:
        unit.status.waiting(f'waiting on {db.endpoint_name}')
        flags.db_ready.clear()
    else:
        flags.db_ready.set()
        if db.is_changed:
            configure_vanilla(db)


def configure_vanilla(db):
    flags.vanilla_started.clear()
    unit.status.maintenance('configuring vanilla')
    templating.render(source='vanilla_config.php',
                      target='/var/www/vanilla/conf/config.php',
                      owner='www-data',
                      perms=0o775,
                      context={
                          'db': db,
                      })
    uid = pwd.getpwnam('www-data').pw_uid
    os.chown('/var/www/vanilla/cache', uid, -1)
    os.chown('/var/www/vanilla/uploads', uid, -1)
    # Tell the apache_php layer that we're ready to start,
    # and what state to trigger when it's complete.
    apache_php.start_site('vanilla',
                          started=flags.vanilla_started)
    unit.status.maintenance('starting vanilla')
    # Inform the DB layer that we have handled the changed data.
    db.clear_changed()


def report_running(self):
    unit.status.active('running')
    if unit.is_leader:
        app.status.active('running')


# has to come after function definitions :(
flags.reactions = {
    flags.not_set(flags.vanilla_installed): install,
    flags.all(flags.apache_installed,
              flags.not_set(flags.apache_configured)): configure_apache,
    flags.apache_configured: check_db,
    flags.vanilla_started: report_running,
}
