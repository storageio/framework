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

import logging
from ConfigParser import RawConfigParser
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagementEx
from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.remote import Remote
from ovs.extensions.generic.system import System
from ovs.plugin.provider.service import Service
from StringIO import StringIO

import os
import tempfile
import time


class ArakoonNodeConfig(object):
    """
    cluster node config parameters
    """
    def __init__(self, name, ip, client_port, messaging_port, log_dir, home, tlog_dir):
        """
        Initializes a new Config entry for a single Node
        """
        self.name = name
        self.ip = ip
        self.client_port = client_port
        self.messaging_port = messaging_port
        self.tlog_compression = 'snappy'
        self.log_level = 'info'
        self.log_dir = log_dir
        self.home = home
        self.tlog_dir = tlog_dir
        self.fsync = True

    def __hash__(self):
        """
        Defines a hashing equivalent for a given ArakoonNodeConfig
        """
        return hash(self.name)

    def __eq__(self, other):
        """
        Checks whether two objects are the same.
        """
        if not isinstance(other, ArakoonNodeConfig):
            return False
        return self.__hash__() == other.__hash__()

    def __ne__(self, other):
        """
        Checks whether two objects are not the same.
        """
        if not isinstance(other, ArakoonNodeConfig):
            return True
        return not self.__eq__(other)


class ArakoonClusterConfig():
    """
    contains cluster config parameters
    """
    ARAKOON_CONFIG_DIR = '/opt/OpenvStorage/config/arakoon/{0}'
    ARAKOON_CONFIG_FILE = '/opt/OpenvStorage/config/arakoon/{0}/{0}.cfg'

    def __init__(self, cluster_id, plugins=None):
        """
        Initializes an empty Cluster Config
        """
        self.cluster_id = cluster_id
        self._dir = ArakoonClusterConfig.ARAKOON_CONFIG_DIR.format(self.cluster_id)
        self._filename = ArakoonClusterConfig.ARAKOON_CONFIG_FILE.format(self.cluster_id)
        self.nodes = []
        self._plugins = []
        if isinstance(plugins, list):
            self._plugins = plugins
        elif isinstance(plugins, basestring):
            self._plugins.append(plugins)

    def load_config(self, client):
        """
        Reads a configuration from reality
        """
        contents = client.file_read(self._filename)
        parser = RawConfigParser()
        parser.readfp(StringIO(contents))

        if parser.has_option('global', 'plugins'):
            self._plugins = [plugin.strip() for plugin in parser.get('global', 'plugins').split(',')]
        for node in parser.get('global', 'cluster').split(','):
            node = node.strip()
            self.nodes.append(ArakoonNodeConfig(name=node,
                                                ip=parser.get(node, 'ip'),
                                                client_port=parser.get(node, 'client_port'),
                                                messaging_port=parser.get(node, 'messaging_port'),
                                                log_dir=parser.get(node, 'log_dir'),
                                                home=parser.get(node, 'home'),
                                                tlog_dir=parser.get(node, 'tlog_dir')))

    def export(self):
        """
        Exports the current configuration to a python dict
        """
        data = {'global': {'cluster_id': self.cluster_id,
                           'cluster': ','.join(sorted(node.name for node in self.nodes)),
                           'plugins': ','.join(sorted(self._plugins))}}
        for node in self.nodes:
            data[node.name] = {'name': node.name,
                               'ip': node.ip,
                               'client_port': node.client_port,
                               'messaging_port': node.messaging_port,
                               'tlog_compression': node.tlog_compression,
                               'log_level': node.log_level,
                               'log_dir': node.log_dir,
                               'home': node.home,
                               'tlog_dir': node.tlog_dir,
                               'fsync': 'true' if node.fsync else 'false'}
        return data

    def write_config(self, client):
        """
        Writes the configuration down to in the format expected by Arakoon
        """
        (temp_handle, temp_filename) = tempfile.mkstemp()
        contents = RawConfigParser()
        data = self.export()
        for section in data:
            contents.add_section(section)
            for item in data[section]:
                contents.set(section, item, data[section][item])
        with open(temp_filename, 'wb') as config_file:
            contents.write(config_file)
        client.dir_create(self._dir)
        client.file_upload(self._filename, temp_filename)
        os.remove(temp_filename)

    def delete_config(self, client):
        """
        Deletes a configuration file
        """
        client.dir_delete(self._dir)


class ArakoonInstaller():
    """
    class to dynamically install/(re)configure arakoon cluster
    """
    ARAKOON_LOG_DIR = '/var/log/arakoon/{0}'
    ARAKOON_HOME_DIR = '{0}/arakoon/{1}'
    ARAKOON_TLOG_DIR = '{0}/tlogs/{1}'
    ARAKOON_CONFIG_DIR = '/opt/OpenvStorage/config/arakoon'
    ARAKOON_CONFIG_FILE = '/opt/OpenvStorage/config/arakoon/{0}/{0}.cfg'

    ARAKOON_UPSTART_FILE = '/etc/init/ovs-arakoon-{0}.conf'
    ARAKOON_UPSTART = """
description "Arakoon upstart"

start on (local-filesystems and started networking)
stop on runlevel [016]

kill timeout 60
respawn
respawn limit 10 5
console log
setuid ovs
setgid ovs

env PYTHONPATH=/opt/OpenvStorage
chdir /opt/OpenvStorage

exec /usr/bin/python2 /opt/OpenvStorage/ovs/extensions/db/arakoon/ArakoonManagement.py --start --cluster {0}
"""

    def __init__(self):
        """
        ArakoonInstaller should not be instantiated
        """
        raise RuntimeError('ArakoonInstaller is a complete static helper class')

    @staticmethod
    def create_cluster(cluster_name, ip, exclude_ports, plugins=None):
        """
        Creates a cluster
        """
        client = SSHClient(ip, username='ovs')
        base_dir = client.config_read('ovs.core.db.arakoon.location').rstrip('/')
        port_range = client.config_read('ovs.ports.arakoon')
        ports = System.get_free_ports(port_range, exclude_ports, 2, client)
        node_name = System.get_my_machine_id(client)

        config = ArakoonClusterConfig(cluster_name, plugins)
        if not [node.name for node in config.nodes if node.name == node_name]:
            config.nodes.append(ArakoonNodeConfig(name=node_name,
                                                  ip=ip,
                                                  client_port=ports[0],
                                                  messaging_port=ports[1],
                                                  log_dir=ArakoonInstaller.ARAKOON_LOG_DIR.format(cluster_name),
                                                  home=ArakoonInstaller.ARAKOON_HOME_DIR.format(base_dir, cluster_name),
                                                  tlog_dir=ArakoonInstaller.ARAKOON_TLOG_DIR.format(base_dir, cluster_name)))
        ArakoonInstaller._deploy(config)
        return {'client_port': ports[0],
                'messaging_port': ports[1]}

    @staticmethod
    def delete_cluster(cluster_name, ip):
        """
        Deletes a complete cluster
        """
        config = ArakoonClusterConfig(cluster_name)
        config.load_config(SSHClient(ip, username='ovs'))

        # Cleans up a complete cluster (remove services, directories and configuration files)
        for node in config.nodes:
            ArakoonInstaller._destroy_node(config, node)

    @staticmethod
    def extend_cluster(master_ip, new_ip, cluster_name, exclude_ports):
        """
        Extends a cluster to a given new node
        """
        client = SSHClient(master_ip, username='ovs')
        config = ArakoonClusterConfig(cluster_name)
        config.load_config(client)

        client = SSHClient(new_ip, username='ovs')
        base_dir = client.config_read('ovs.core.db.arakoon.location').rstrip('/')
        port_range = client.config_read('ovs.ports.arakoon')
        ports = System.get_free_ports(port_range, exclude_ports, 2, client)
        node_name = System.get_my_machine_id(client)

        if not [node.name for node in config.nodes if node.name == node_name]:
            config.nodes.append(ArakoonNodeConfig(name=node_name,
                                                  ip=new_ip,
                                                  client_port=ports[0],
                                                  messaging_port=ports[1],
                                                  log_dir=ArakoonInstaller.ARAKOON_LOG_DIR.format(cluster_name),
                                                  home=ArakoonInstaller.ARAKOON_HOME_DIR.format(base_dir, cluster_name),
                                                  tlog_dir=ArakoonInstaller.ARAKOON_TLOG_DIR.format(base_dir, cluster_name)))
        ArakoonInstaller._deploy(config)
        return {'client_port': ports[0],
                'messaging_port': ports[1]}

    @staticmethod
    def shrink_cluster(remaining_node_ip, deleted_node_ip, cluster_name):
        """
        Removes a node from a cluster, the old node will become a slave
        """
        client = SSHClient(remaining_node_ip, username='ovs')
        config = ArakoonClusterConfig(cluster_name)
        config.load_config(client)

        for node in config.nodes[:]:
            if node.ip == deleted_node_ip:
                config.nodes.remove(node)
                ArakoonInstaller._destroy_node(config, node)
        ArakoonInstaller._deploy(config)
        ArakoonInstaller.deploy_to_slave(remaining_node_ip, deleted_node_ip, cluster_name)

    @staticmethod
    def _destroy_node(config, node):
        """
        Cleans up a single node (remove services, directories and configuration files)
        """
        # Removes services for a cluster on a given node
        ovs_client = SSHClient(node.ip, username='ovs')
        root_client = SSHClient(node.ip, username='root')
        ArakoonInstaller.stop(config.cluster_id, client=root_client)
        ArakoonInstaller.remove(config.cluster_id, client=root_client)

        # Cleans all directories on a given node
        ovs_client.dir_delete([node.log_dir, node.tlog_dir, node.home])

        # Removes a configuration file from a node
        config.delete_config(ovs_client)

    @staticmethod
    def _deploy(config):
        """
        Deploys a complete cluster: Distributing the configuration files, creating directories and services
        """
        for node in config.nodes:
            ovs_client = SSHClient(node.ip, username='ovs')
            root_client = SSHClient(node.ip, username='root')

            # Distributes a configuration file to all its nodes
            config.write_config(ovs_client)

            # Create dirs as root because mountpoint /mnt/cache1 is typically owned by root
            abs_paths = [node.log_dir, node.tlog_dir, node.home]
            root_client.dir_create(abs_paths)
            root_client.dir_chmod(abs_paths, 0755, recursive=True)
            root_client.dir_chown(abs_paths, 'ovs', 'ovs', recursive=True)

            # Creates services for/on all nodes in the config
            (temp_handle, temp_filename) = tempfile.mkstemp()
            config_file = ArakoonInstaller.ARAKOON_UPSTART_FILE.format(config.cluster_id)
            contents = ArakoonInstaller.ARAKOON_UPSTART.format(config.cluster_id)
            with open(temp_filename, 'wb') as f:
                f.write(contents)
            ovs_client.dir_create(os.path.dirname(config_file))
            root_client.file_upload(config_file, temp_filename)
            os.remove(temp_filename)

    @staticmethod
    def start(cluster_name, client):
        """
        Starts an arakoon cluster
        """
        if Service.get_service_status('arakoon-{0}'.format(cluster_name), client=client) is False:
            Service.start_service('arakoon-{0}'.format(cluster_name), client=client)

    @staticmethod
    def stop(cluster_name, client):
        """
        Stops an arakoon service
        """
        if Service.get_service_status('arakoon-{0}'.format(cluster_name), client=client) is True:
            Service.stop_service('arakoon-{0}'.format(cluster_name), client=client)

    @staticmethod
    def remove(cluster_name, client):
        """
        Removes an arakoon service
        """
        Service.remove_service('arakoon-{0}'.format(cluster_name), client=client)

    @staticmethod
    def deploy_to_slave(master_ip, slave_ip, cluster_name):
        """
        Deploys the configuration file to a slave
        """
        client = SSHClient(master_ip, username='ovs')
        config = ArakoonClusterConfig(cluster_name)
        config.load_config(client)
        client = SSHClient(slave_ip, username='ovs')
        config.write_config(client)

    @staticmethod
    def remove_from_slave(master_ip, slave_ip, cluster_name):
        """
        Removes everything related to a given cluster from the slave
        """
        client = SSHClient(master_ip, username='ovs')
        config = ArakoonClusterConfig(cluster_name)
        config.load_config(client)
        client = SSHClient(slave_ip, username='ovs')
        config.delete_config(client)

    @staticmethod
    def wait_for_cluster(cluster_name):
        """
        Waits for an Arakoon cluster to be available (by sending a nop)
        """
        from ovs.extensions.db.arakoon.arakoon.ArakoonExceptions import ArakoonSockReadNoBytes

        last_exception = None
        tries = 3
        while tries > 0:
            try:
                cluster_object = ArakoonManagementEx().getCluster(str(cluster_name))
                client = cluster_object.getClient()
                client.nop()
                return True
            except ArakoonSockReadNoBytes as exception:
                last_exception = exception
                tries -= 1
                time.sleep(1)
        raise last_exception

    @staticmethod
    def restart_cluster_add(cluster_name, current_ips, new_ip):
        """
        Execute a (re)start sequence after adding a new node to a cluster.
        """
        # Make sure all nodes are correctly (re)started
        loglevel = logging.root.manager.disable  # Workaround for disabling Arakoon logging
        logging.disable('WARNING')

        with Remote(new_ip, [ArakoonManagementEx]) as remote:
            cluster = remote.ArakoonManagementEx().getCluster(cluster_name)
            cluster.catchup_node()

        threshold = 2 if new_ip in current_ips else 1
        for ip in current_ips:
            if ip == new_ip:
                continue
            client = SSHClient(ip, username='root')
            ArakoonInstaller.stop(cluster_name, client=client)
            ArakoonInstaller.start(cluster_name, client=client)
            if len(current_ips) > threshold:  # A two node cluster needs all nodes running
                ArakoonInstaller.wait_for_cluster(cluster_name)
        new_client = SSHClient(new_ip, username='root')
        ArakoonInstaller.start(cluster_name, client=new_client)
        ArakoonInstaller.wait_for_cluster(cluster_name)
        logging.disable(loglevel)  # Restore workaround

    @staticmethod
    def restart_cluster_remove(cluster_name, remaining_ips):
        """
        Execute a restart sequence after removing a node from a cluster
        """
        loglevel = logging.root.manager.disable  # Workaround for disabling Arakoon logging
        logging.disable('WARNING')
        for ip in remaining_ips:
            client = SSHClient(ip, username='root')
            ArakoonInstaller.stop(cluster_name, client=client)
            ArakoonInstaller.start(cluster_name, client=client)
            if len(remaining_ips) > 2:  # A two node cluster needs all nodes running
                ArakoonInstaller.wait_for_cluster(cluster_name)
        logging.disable(loglevel)  # Restore workaround
