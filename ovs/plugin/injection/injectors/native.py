# Copyright 2014 CloudFounders NV
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Native Injector module
"""
import os
from ovs.extensions.generic.sshclient import SSHClient
from ConfigParser import RawConfigParser
from subprocess import check_output, CalledProcessError


class Injector(object):
    """
    Injector class, provides all logic to inject. However, the unittest injector
    only provides functionality required in the unittests
    """

    def __init__(self):
        """
        This class should be fully static
        """
        raise RuntimeError('This class should not be instantiated.')

    @staticmethod
    def inject_configuration(provider):
        """ Injects the Config module """
        def _get(key):
            filename, section, item = key.split('.', 2)
            config = RawConfigParser()
            config.read('/opt/OpenvStorage/config/{0}.cfg'.format(filename))
            return config.get(section, item)

        def _set(key, value):
            filename, section, item = key.split('.', 2)
            config = RawConfigParser()
            config.read('/opt/OpenvStorage/config/{0}.cfg'.format(filename))
            config.set(section, item, value)
            with open('/opt/OpenvStorage/config/{0}.cfg'.format(filename), 'w') as config_file:
                config.write(config_file)

        def _get_int(key):
            return int(_get(key))

        provider.get = staticmethod(_get)
        provider.getInt = staticmethod(_get_int)
        provider.set = staticmethod(_set)
        return provider

    @staticmethod
    def inject_remote(provider):
        """ Injects the remote module """
        class Cuisine:
            import cuisine
            import fabric

            api = cuisine
            fabric = fabric.api

        provider.fabric = Cuisine().fabric
        provider.cuisine = Cuisine()

        return provider

    @staticmethod
    def inject_service(provider):
        """ Injects the Service module """

        def _service_exists(name, client, path):
            if path is None:
                path = '/etc/init/'
            file_to_check = '{0}{1}.conf'.format(path, name)
            return client.file_exists(file_to_check)

        def _get_name(name, client, path=None):
            if _service_exists(name, client, path):
                return name
            name = 'ovs-{0}'.format(name)
            if _service_exists(name, client, path):
                return name
            raise ValueError('Service {0} could not be found.'.format(name))

        def add_service(name, client, params=None):
            if params is None:
                params = {}

            name = _get_name(name, client, '/opt/OpenvStorage/config/templates/upstart/')
            template_dir = '/opt/OpenvStorage/config/templates/upstart/{0}'
            upstart_dir = '/etc/init/{0}'
            upstart_conf = '{0}.conf'.format(name)
            template_conf = client.file_read(template_dir.format(upstart_conf))

            for key, value in params.iteritems():
                template_conf = template_conf.replace(key, value)

            client.file_write(upstart_dir.format(upstart_conf), template_conf)

        def get_service_status(name, client):
            try:
                name = _get_name(name, client)
                output = client.run('status {0}'.format(name))
                if 'start' in output:
                    return True
                if 'stop' in output:
                    return False
                return False
            except CalledProcessError:
                raise Exception('Retrieving status for service "{0}" failed'.format(name))

        def remove_service(name, client):
            # remove upstart.conf file
            name = _get_name(name, client)
            client.file_delete('/etc/init/{0}.conf'.format(name))
            client.file_delete('/etc/init/{0}.override'.format(name))

        def disable_service(name, client):
            name = _get_name(name, client)
            client.run('echo "manual" > /etc/init/{0}.override'.format(name))

        def enable_service(name, client):
            name = _get_name(name, client)
            client.file_delete('/etc/init/{0}.override'.format(name))

        def start_service(name, client):
            try:
                name = _get_name(name, client)
                output = client.run('start {0}'.format(name))
            except CalledProcessError as cpe:
                output = cpe.output
            return output

        def stop_service(name, client):
            try:
                name = _get_name(name, client)
                output = client.run('stop {0}'.format(name))
            except CalledProcessError as cpe:
                output = cpe.output
            return output

        def restart_service(name, client):
            try:
                name = _get_name(name, client)
                output = client.run('restart {0}'.format(name))
            except CalledProcessError as cpe:
                output = cpe.output
            return output

        def has_service(name, client):
            try:
                _get_name(name, client)
                return True
            except ValueError:
                return False

        provider.add_service = staticmethod(add_service)
        provider.remove_service = staticmethod(remove_service)
        provider.get_service_status = staticmethod(get_service_status)
        provider.disable_service = staticmethod(disable_service)
        provider.enable_service = staticmethod(enable_service)
        provider.start_service = staticmethod(start_service)
        provider.stop_service = staticmethod(stop_service)
        provider.restart_service = staticmethod(restart_service)
        provider.has_service = staticmethod(has_service)
        return provider

    @staticmethod
    def inject_process(provider):
        """ Injects the Process module """

        def check_process(name):
            output = check_output('ps aux | grep -v grep | grep {0} || true'.format(name), shell = True)
            # It returns 1 if the process is not running, else it returns 0. Don't ask questions...
            return 1 if name not in output else 0

        provider.checkProcess = staticmethod(check_process)
        return provider

    @staticmethod
    def inject_package(provider):
        """ Injects the Package module """

        def _get_version(package):
            return check_output("dpkg -s {0} | grep Version | cut -d ' ' -f 2".format(package), shell=True).strip()

        def get_versions():
            versions = {}
            for package in ['openvstorage', 'openvstorage-backend', 'volumedriver-server', 'volumedriver-base', 'alba', 'alba-asdmanager']:
                version_info = _get_version(package)
                if version_info:
                    versions[package] = version_info
            return versions

        provider.get_versions = staticmethod(get_versions)
        return provider
