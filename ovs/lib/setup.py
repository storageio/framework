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
Module for SetupController
"""

import os
import re
import sys
import time
import ConfigParser
import urllib2
import base64
from subprocess import check_output

from ovs.extensions.generic.sshclient import SSHClient
from ovs.extensions.generic.interactive import Interactive
from ovs.log.logHandler import LogHandler


logger = LogHandler('lib', name='setup')
logger.logger.propagate = False

# @TODO: Make the setup_node re-entrant
# @TODO: Make it possible to run as a non-privileged user
# @TODO: Node password identical for all nodes


class SetupController(object):
    """
    This class contains all logic for setting up an environment, installed with system-native packages
    """

    PARTITION_DEFAULTS = {'device': 'DIR_ONLY', 'percentage': 'NA', 'label': 'cache1'}

    @staticmethod
    def setup_node(ip=None, force_type=None, verbose=False):
        """
        Sets up a node, post installation
        """

        default_hypervisor_username = 'root'

        try:
            print Interactive.boxed_message(['Open vStorage Setup'])
            logger.info('Starting Open vStorage Setup')

            # Prepare variables
            auto_config = False
            disk_layout = {}
            target_password = None
            join_cluster = False
            cluster_name = None
            cluster_ip = ''
            master_ip = None
            node_type = None  # in ['master', 'extra']
            nodes = []
            hypervisor_type = ''
            hypervisor_name = ''
            hypervisor_ip = ''
            hypervisor_username = ''
            hypervisor_password = ''
            arakoon_mountpoint = ''
            rabbitmq_server_config = """
[
   {rabbit, [{tcp_listeners, [%(broker_port)s]},
             {default_user, <<"%(broker_username)s">>},
             {default_pass, <<"%(broker_password)s">>}]}
].
"""
            # Support non-interactive setup
            preconfig = '/tmp/openvstorage_preconfig.cfg'
            if os.path.exists(preconfig):
                config = ConfigParser.ConfigParser()
                config.read(preconfig)
                ip = config.get('setup', 'target_ip')
                target_password = config.get('setup', 'target_password')
                cluster_ip = config.get('setup', 'cluster_ip')
                cluster_name = str(config.get('setup', 'cluster_name'))
                join_cluster = config.getboolean('setup', 'join_cluster')
                master_ip = config.get('setup', 'master_ip')
                hypervisor_type = config.get('setup', 'hypervisor_type')
                hypervisor_name = config.get('setup', 'hypervisor_name')
                hypervisor_ip = config.get('setup', 'hypervisor_ip')
                hypervisor_username = config.get('setup', 'hypervisor_username')
                hypervisor_password = config.get('setup', 'hypervisor_password')
                arakoon_mountpoint = config.get('setup', 'arakoon_mountpoint')
                verbose = config.getboolean('setup', 'verbose')
                auto_config = config.get('setup', 'auto_config')
                disk_layout = eval(config.get('setup', 'disk_layout'))

            if force_type is not None:
                force_type = force_type.lower()
                if force_type not in ['master', 'extra']:
                    raise ValueError("The force_type parameter should be 'master' or 'extra'.")
                node_type = force_type

            # Create connection to target node
            print '\n+++ Setting up connections +++\n'
            logger.info('Setting up connections')
            if ip is None:
                ip = '127.0.0.1'
            if target_password is None:
                node_string = 'this node' if ip == '127.0.0.1' else ip
                target_node_password = Interactive.ask_password('Enter the root password for {0}'.format(node_string))
            else:
                target_node_password = target_password
            target_client = SSHClient.load(ip, target_node_password)
            if verbose:
                logger.debug('Verbose mode')
                from ovs.plugin.provider.remote import Remote
                Remote.cuisine.fabric.output['running'] = True
            logger.debug('Target client loaded')

            # Check whether running local or remote
            command = "ip a | grep link/ether | sed 's/\s\s*/ /g' | cut -d ' ' -f 3 | sed 's/://g'"
            unique_id = sorted(target_client.run(command).strip().split('\n'))[0][:12]
            local_unique_id = sorted(check_output(command, shell=True).strip().split('\n'))[0][:12]
            remote_install = unique_id != local_unique_id
            logger.debug('{0} installation'.format('Remote' if remote_install else 'Local'))

            # Collect information about the cluster to join
            print '\n+++ Collecting cluster information +++\n'
            current_cluster_names = []
            clusters = []
            discovery_result = SetupController._discover_nodes(target_client)
            if discovery_result:
                clusters = discovery_result.keys()
                current_cluster_names = clusters[:]
                logger.debug('Cluster names: {0}'.format(current_cluster_names))
            else:
                print 'No existing Open vStorage clusters are found.'
                logger.debug('No clusters found')
                if remote_install is True:
                    raise RuntimeError('A remote install requires to join an existing cluster.')

            avahi_filename = '/etc/avahi/services/ovs_cluster.service'
            local_cluster_name = None
            if remote_install is True:
                if not os.path.exists(avahi_filename):
                    raise RuntimeError('A remote install can only be executed from a configured node.')
                with open(avahi_filename, 'r') as avahi_file:
                    avahi_contents = avahi_file.read()
                match_groups = re.search('>ovs_cluster_(?P<cluster>[^_]+)_.+?<', avahi_contents).groupdict()
                if 'cluster' not in match_groups:
                    raise RuntimeError('Invalid OVS avahi service file on local node.')
                local_cluster_name = match_groups['cluster']

            node_name = target_client.run('hostname')
            logger.debug('Current host: {0}'.format(node_name))
            if cluster_name is None:
                if len(clusters) > 0:
                    dont_join = "Don't join any of these clusters."
                    if local_cluster_name is None:
                        logger.debug('Manual cluster selection')
                        if force_type in [None, 'master']:
                            clusters.append(dont_join)
                        print 'Following Open vStorage clusters are found.'
                        cluster_name = Interactive.ask_choice(clusters, 'Select a cluster to join')
                    else:
                        logger.debug('Local cluster name selected')
                        cluster_name = local_cluster_name
                        print "Selected local cluster '{0}' for remote installation".format(cluster_name)
                    if cluster_name != dont_join:
                        logger.debug('Cluster {0} selected'.format(cluster_name))
                        nodes = [node_property['ip'] for node_property in discovery_result[cluster_name].values()]
                        if node_name in discovery_result[cluster_name].keys():
                            continue_install = Interactive.ask_yesno(
                                '{0} already exists in cluster {1}. Do you want to continue?'.format(
                                    node_name, cluster_name
                                ), default_value=True
                            )
                            if continue_install is False:
                                raise ValueError('Duplicate node name found.')
                        master_nodes = [this_node_name for this_node_name, node_properties in discovery_result[cluster_name].iteritems()
                                        if node_properties.get('type', None) == 'master']
                        if len(master_nodes) == 0:
                            raise RuntimeError('No master node could be found in cluster {0}'.format(cluster_name))
                        master_ip = discovery_result[cluster_name][master_nodes[0]]['ip']
                        join_cluster = True
                    else:
                        cluster_name = None
                        logger.debug('No cluster will be joined')
                elif force_type is not None and force_type != 'master':
                    raise RuntimeError('No clusters were found. Only a Master node can be set up.')

                if join_cluster is False and cluster_name is None:
                    while True:
                        cluster_name = Interactive.ask_string('Please enter the cluster name')
                        if cluster_name in current_cluster_names:
                            print 'The new cluster name should be unique.'
                        elif '_' in cluster_name:
                            print "The new cluster name should not contain '_'."
                        else:
                            break

            else:  # Automated install
                logger.debug('Automated installation')
                if cluster_name in discovery_result:
                    nodes = [node_property['ip'] for node_property in discovery_result[cluster_name].values()]

            # Creating filesystems
            print '\n+++ Creating filesystems +++\n'
            logger.info('Creating filesystems')
            disk_layout = SetupController.apply_flexible_disk_layout(target_client, auto_config, disk_layout)
            mountpoints = disk_layout.keys()
            mountpoints.sort()

            # Get target grid ip
            print '\n+++ Collecting generic information +++\n'
            logger.info('Collecting generic information')
            if not target_client.file_exists('/opt/OpenvStorage/config/ovs.cfg'):
                raise RuntimeError("The 'openvstorage' package is not installed on {0}".format(ip))

            config_filename = '/opt/OpenvStorage/config/ovs.cfg'
            ovs_config = SetupController._remote_config_read(target_client, config_filename)
            ovs_config.set('core', 'uniqueid', unique_id)
            SetupController._remote_config_write(target_client, config_filename, ovs_config)

            ipaddresses = target_client.run(
                "ip a | grep 'inet ' | sed 's/\s\s*/ /g' | cut -d ' ' -f 3 | cut -d '/' -f 1"
            ).strip().split('\n')
            ipaddresses = [found_ip.strip() for found_ip in ipaddresses if found_ip.strip() != '127.0.0.1']
            if not cluster_ip:
                cluster_ip = Interactive.ask_choice(ipaddresses, 'Select the public ip address of {0}'.format(node_name))
            if cluster_ip not in nodes:
                nodes.append(cluster_ip)
            logger.debug('Cluster ip is selected as {0}'.format(cluster_ip))
            ip = cluster_ip
            # Collecting hypervisor data
            possible_hypervisor = SetupController._discover_hypervisor(target_client)
            if not hypervisor_type:
                hypervisor_type = Interactive.ask_choice(['VMWARE', 'KVM'],
                                                         question='Which type of hypervisor is this Storage Router backing?',
                                                         default_value=possible_hypervisor)
                logger.debug('Selected hypervisor type {0}'.format(hypervisor_type))
            default_name = 'esxi' if hypervisor_type == 'VMWARE' else 'kvm'
            if not hypervisor_name:
                hypervisor_name = Interactive.ask_string('Enter hypervisor hostname', default_value=default_name)
            if hypervisor_type == 'VMWARE':
                first_request = True  # If parameters are wrong, we need to re-ask it
                while True:
                    if not hypervisor_ip or not first_request:
                        hypervisor_ip = Interactive.ask_string('Enter hypervisor ip address', default_value=hypervisor_ip)
                    if not hypervisor_username or not first_request:
                        hypervisor_username = Interactive.ask_string('Enter hypervisor username', default_value=default_hypervisor_username)
                    if not hypervisor_password or not first_request:
                        hypervisor_password = Interactive.ask_password('Enter hypervisor root password')
                    try:
                        request = urllib2.Request('https://{0}/mob'.format(hypervisor_ip))
                        auth = base64.encodestring('{0}:{1}'.format(hypervisor_username, hypervisor_password)).replace('\n', '')
                        request.add_header("Authorization", "Basic %s" % auth)
                        urllib2.urlopen(request).read()
                        break
                    except Exception as ex:
                        first_request = False
                        print 'Could not connect to {0}: {1}'.format(hypervisor_ip, ex)
            elif hypervisor_type == 'KVM':
                # In case of KVM, the StorageRouter is the pMachine, so credentials are shared.
                hypervisor_ip = cluster_ip
                hypervisor_username = 'root'
                hypervisor_password = target_node_password
            logger.debug('Hypervisor at {0} with username {1}'.format(hypervisor_ip, hypervisor_username))

            # Ask for Arakoon's db location
            if not arakoon_mountpoint:
                arakoon_mountpoint = Interactive.ask_choice(mountpoints, question='Select arakoon database mountpoint',
                                                            default_value=Interactive.find_in_list(mountpoints, 'db'))
            mountpoints.remove(arakoon_mountpoint)
            ovs_config.set('core', 'db.arakoon.location', arakoon_mountpoint)
            SetupController._remote_config_write(target_client, config_filename, ovs_config)

            print '\n+++ Adding basic configuration +++\n'
            logger.info('Adding basic configuration')

            # Exchange ssh keys
            print 'Exchanging SSH keys'
            logger.info('Exchanging SSH keys')
            passwords = None
            prev_node_password = ''
            for node in nodes:
                if passwords is None:
                    if not target_password:
                        prev_node_password = Interactive.ask_password('Enter root password for {0}'.format(node))
                        logger.debug('Custom password for {0}'.format(node))
                    else:
                        prev_node_password = target_password
                    passwords = {node: prev_node_password}
                else:
                    if not target_password:
                        this_node_password = Interactive.ask_password(
                            'Enter root password for {0}, just press enter if identical as above'.format(node)
                        )
                        if this_node_password == '':
                            logger.debug('Identical password for {0}'.format(node))
                            this_node_password = prev_node_password
                    else:
                        this_node_password = target_password
                    passwords[node] = this_node_password
                    prev_node_password = this_node_password
            root_ssh_folder = '/root/.ssh'
            ovs_ssh_folder = '/opt/OpenvStorage/.ssh'
            public_key_filename = '{0}/id_rsa.pub'
            authorized_keys_filename = '{0}/authorized_keys'
            known_hosts_filename = '{0}/known_hosts'
            authorized_keys = ''
            for node in nodes:
                node_client = SSHClient.load(node, passwords[node])
                root_pub_key = node_client.file_read(public_key_filename.format(root_ssh_folder))
                ovs_pub_key = node_client.file_read(public_key_filename.format(ovs_ssh_folder))
                authorized_keys += '{0}\n{1}\n'.format(root_pub_key, ovs_pub_key)
            for node in nodes:
                node_client = SSHClient.load(node, passwords[node])
                node_client.file_write(authorized_keys_filename.format(root_ssh_folder), authorized_keys)
                node_client.file_write(authorized_keys_filename.format(ovs_ssh_folder), authorized_keys)
                node_client.run(
                    'ssh-keyscan -H {0} >> {1}'.format(' '.join(nodes), known_hosts_filename.format(root_ssh_folder))
                )
                node_client.run(
                    'su - ovs -c "ssh-keyscan -H {0} >> {1}"'.format(' '.join(nodes), known_hosts_filename.format(ovs_ssh_folder))
                )

            # Define services
            model_services = ['memcached', 'arakoon-ovsdb']
            master_services = model_services + ['rabbitmq', 'arakoon-voldrv']
            extra_node_services = ['workers', 'volumerouter-consumer']
            master_node_services = master_services + ['scheduled-tasks', 'snmp', 'webapp-api', 'nginx', 'volumerouter-consumer'] + extra_node_services

            arakoon_client_config = '/opt/OpenvStorage/config/arakoon/{0}/{0}_client.cfg'
            arakoon_server_config = '/opt/OpenvStorage/config/arakoon/{0}/{0}.cfg'
            arakoon_local_nodes = '/opt/OpenvStorage/config/arakoon/{0}/{0}_local_nodes.cfg'
            arakoon_clusters = {'ovsdb': 8870, 'voldrv': 8872}
            generic_configfiles = {'/opt/OpenvStorage/config/memcacheclient.cfg': 11211,
                                   '/opt/OpenvStorage/config/rabbitmqclient.cfg': 5672}

            # Deciding master/extra
            print 'Analyzing cluster layout'
            logger.info('Analyzing cluster layout')
            unique_id = ovs_config.get('core', 'uniqueid')
            join_masters = False
            if join_cluster:
                # Updating configuration files and copy them around
                for cluster in arakoon_clusters.keys():
                    master_client = SSHClient.load(master_ip)
                    config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                    cluster_nodes = [node.strip() for node in config.get('global', 'cluster').split(',')]
                    logger.debug('{0} nodes for cluster {1} found'.format(len(cluster_nodes), cluster))
                    if (len(cluster_nodes) < 3 or force_type == 'master') and force_type != 'extra':
                        join_masters = True
            else:
                join_masters = True
            if node_type is None:
                node_type = 'master' if join_masters is True else 'extra'

            # Elastic search setup
            print 'Configuring logstash{0}'.format(' and elastic search' if join_masters else '')
            target_client = SSHClient.load(cluster_ip)
            if join_masters:
                SetupController._add_service(target_client, 'elasticsearch')
                config_file = '/etc/elasticsearch/elasticsearch.yml'
                SetupController._change_service_state(target_client, 'elasticsearch', 'stop')
                target_client.run('cp /opt/OpenvStorage/config/elasticsearch.yml /etc/elasticsearch/')
                target_client.run('mkdir -p /opt/data/elasticsearch/work')
                target_client.run('chown -R elasticsearch:elasticsearch /opt/data/elasticsearch*')
                SetupController._replace_param_in_config(target_client,
                                                         config_file,
                                                         '<CLUSTER_NAME>',
                                                         'ovses_{0}'.format(cluster_name),
                                                         add=False)
                SetupController._replace_param_in_config(target_client,
                                                         config_file,
                                                         '<NODE_NAME>',
                                                         node_name)
                SetupController._replace_param_in_config(target_client,
                                                         config_file,
                                                         '<NETWORK_PUBLISH>',
                                                         cluster_ip)
                SetupController._change_service_state(target_client, 'elasticsearch', 'start')

            SetupController._replace_param_in_config(target_client,
                                                     '/etc/logstash/conf.d/indexer.conf',
                                                     '<CLUSTER_NAME>',
                                                     'ovses_{0}'.format(cluster_name))
            SetupController._change_service_state(target_client, 'logstash', 'restart')

            print 'Adding services'
            logger.info('Adding services')
            params = {'<ARAKOON_NODE_ID>': unique_id,
                      '<MEMCACHE_NODE_IP>': cluster_ip,
                      '<WORKER_QUEUE>': unique_id}
            if join_masters:
                for service in master_node_services + ['watcher']:
                    logger.debug('Adding service {0}'.format(service))
                    SetupController._add_service(target_client, service, params)
            else:
                for service in extra_node_services + ['watcher']:
                    logger.debug('Adding service {0}'.format(service))
                    SetupController._add_service(target_client, service, params)

            print 'Updating hosts files'
            logger.debug('Updating hosts files')
            for node in nodes:
                client_node = SSHClient.load(node)
                update_hosts_file = """
from ovs.extensions.generic.system import System
System.update_hosts_file(hostname='%(host)s', ip='%(ip)s')
""" % {'ip': cluster_ip,
       'host': node_name}
                SetupController._exec_python(client_node, update_hosts_file)
                if node == ip:
                    for subnode in nodes:
                        client_node = SSHClient.load(subnode)
                        node_hostname = client_node.run('hostname')
                        update_hosts_file = """
from ovs.extensions.generic.system import System
System.update_hosts_file(hostname='%(host)s', ip='%(ip)s')
""" % {'ip': subnode,
       'host': node_hostname}
                        client = SSHClient.load(ip)
                        SetupController._exec_python(client, update_hosts_file)

            if join_masters:
                print '\n+++ Joining master node +++\n'
                logger.info('Joining master node')

                print 'Stopping services'
                for service in master_services:
                    for node in nodes:
                        node_client = SSHClient.load(node)
                        if SetupController._has_service(node_client, service):
                            SetupController._disable_service(node_client, service)
                            SetupController._change_service_state(node_client, service, 'stop')

                if join_cluster:
                    print 'Joining arakoon cluster'
                    logger.info('Joining arakoon cluster')
                    for cluster in arakoon_clusters.keys():
                        local_config = ConfigParser.ConfigParser()
                        local_config.add_section('global')
                        local_config.set('global', 'cluster', unique_id)
                        target_client = SSHClient.load(cluster_ip)
                        target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/ovsdb', True)
                        target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/voldrv', True)

                        SetupController._remote_config_write(target_client, arakoon_local_nodes.format(cluster), local_config)

                        master_client = SSHClient.load(master_ip)
                        client_config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                        server_config = SetupController._remote_config_read(master_client, arakoon_server_config.format(cluster))
                        for node in nodes:
                            node_client = SSHClient.load(node)
                            node_client.dir_ensure('/opt/OpenvStorage/config/arakoon/{0}'.format(cluster), True)
                            SetupController._configure_arakoon((client_config, server_config), unique_id, cluster, cluster_ip,
                                                               arakoon_clusters[cluster], node_client,
                                                               (arakoon_client_config, arakoon_server_config),
                                                               arakoon_mountpoint)
                else:
                    print 'Setting up first arakoon node'
                    logger.info('Setting up first arakoon node')
                    target_client = SSHClient.load(ip)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/ovsdb', True)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/voldrv', True)
                    for cluster in arakoon_clusters.keys():
                        local_config = ConfigParser.ConfigParser()
                        local_config.add_section('global')
                        local_config.set('global', 'cluster', unique_id)
                        target_client = SSHClient.load(cluster_ip)
                        SetupController._remote_config_write(target_client, arakoon_local_nodes.format(cluster), local_config)

                        client_config = ConfigParser.ConfigParser()
                        server_config = ConfigParser.ConfigParser()
                        SetupController._configure_arakoon((client_config, server_config), unique_id, cluster,
                                                           cluster_ip,
                                                           arakoon_clusters[cluster], target_client,
                                                           (arakoon_client_config, arakoon_server_config),
                                                           arakoon_mountpoint)

                logger.debug('Creating arakoon directories')
                target_client = SSHClient.load(ip)
                for cluster in arakoon_clusters.keys():
                    arakoon_create_directories = """
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagement
arakoon_management = ArakoonManagement()
arakoon_cluster = arakoon_management.getCluster('%(cluster)s')
arakoon_cluster.createDirs(arakoon_cluster.listLocalNodes()[0])
""" % {'cluster': cluster}
                    SetupController._exec_python(target_client, arakoon_create_directories)

                print 'Starting remote RabbitMQ nodes'
                logger.debug('Starting remote RabbitMQ nodes')
                for node in nodes:
                    if node != ip:
                        client_node = SSHClient.load(node)
                        if SetupController._has_service(client_node, 'rabbitmq'):
                            SetupController._change_service_state(client_node, 'rabbitmq', 'start')

                print 'Setting up RabbitMQ'
                logger.debug('Setting up RMQ')
                client = SSHClient.load(ip)

                client.run("""cat > /etc/rabbitmq/rabbitmq.config << EOF
{0}
EOF
""".format(rabbitmq_server_config % {'broker_port': ovs_config.get('core', 'broker.port'),
                                     'broker_username': ovs_config.get('core', 'broker.login'),
                                     'broker_password': ovs_config.get('core', 'broker.password')}))

                rabbitmq_running, rabbitmq_pid = SetupController._is_rabbitmq_running(client)

                if rabbitmq_running and rabbitmq_pid:
                    print('WARNING: an instance of rabbitmq-server is running, this needs to be stopped')
                    client.run('service rabbitmq-server stop')
                    time.sleep(5)
                    try:
                        client.run('kill {0}'.format(rabbitmq_pid))
                        print('Process killed')
                    except SystemExit:
                        print('Process already stopped')

                client.run('rabbitmq-server -detached; sleep 5;')
                users = client.run('rabbitmqctl list_users').split('\r\n')[1:-1]
                users = [usr.split('\t')[0] for usr in users]
                if not 'ovs' in users:
                    client.run('rabbitmqctl add_user {0} {1}'.format(ovs_config.get('core', 'broker.login'),
                                                                     ovs_config.get('core', 'broker.password')))
                    client.run('rabbitmqctl set_permissions {0} ".*" ".*" ".*"'.format(ovs_config.get('core', 'broker.login')))
                client.run('rabbitmqctl stop; sleep 5;')
                if join_masters and join_cluster:
                    # Copy rabbitmq cookie
                    logger.debug('Copying RMQ cookie')
                    rabbitmq_cookie_file = '/var/lib/rabbitmq/.erlang.cookie'
                    master_client = SSHClient.load(master_ip)
                    contents = master_client.file_read(rabbitmq_cookie_file)
                    master_hostname = master_client.run('hostname')
                    client = SSHClient.load(ip)
                    client.dir_ensure(os.path.dirname(rabbitmq_cookie_file), True)
                    client.file_write(rabbitmq_cookie_file, contents)
                    client.file_attribs(rabbitmq_cookie_file, mode=400)
                    client.run('rabbitmq-server -detached; sleep 5; rabbitmqctl stop_app; sleep 5;')
                    client.run('rabbitmqctl join_cluster rabbit@{}; sleep 5;'.format(master_hostname))
                    client.run('rabbitmqctl stop; sleep 5;')

                if join_cluster:
                    print 'Distribute configuration files'
                    logger.info('Distribute configuration files')
                    for config_file, port in generic_configfiles.iteritems():
                        master_client = SSHClient.load(master_ip)
                        config = SetupController._remote_config_read(master_client, config_file)
                        config_nodes = [n.strip() for n in config.get('main', 'nodes').split(',')]
                        if unique_id not in config_nodes:
                            config.set('main', 'nodes', ', '.join(config_nodes + [unique_id]))
                            config.add_section(unique_id)
                            config.set(unique_id, 'location', '{0}:{1}'.format(cluster_ip, port))
                        for node in nodes:
                            node_client = SSHClient.load(node)
                            SetupController._remote_config_write(node_client, config_file, config)
                else:
                    print 'Build configuration files'
                    logger.info('Build configuration files')
                    for config_file, port in generic_configfiles.iteritems():
                        config = ConfigParser.ConfigParser()
                        config.add_section('main')
                        config.set('main', 'nodes', unique_id)
                        config.add_section(unique_id)
                        config.set(unique_id, 'location', '{0}:{1}'.format(cluster_ip, port))
                        client = SSHClient.load(ip)
                        SetupController._remote_config_write(client, config_file, config)

                print 'Update existing vPools'
                logger.info('Update existing vPools')
                for node in nodes:
                    client_node = SSHClient.load(node)
                    update_voldrv = """
import os
from ovs.plugin.provider.configuration import Configuration
from ovs.extensions.storageserver.storagedriver import StorageDriverConfiguration
from ovs.extensions.db.arakoon.ArakoonManagement import ArakoonManagement
arakoon_management = ArakoonManagement()
voldrv_arakoon_cluster_id = 'voldrv'
voldrv_arakoon_cluster = arakoon_management.getCluster(voldrv_arakoon_cluster_id)
voldrv_arakoon_client_config = voldrv_arakoon_cluster.getClientConfig()
configuration_dir = Configuration.get('ovs.core.cfgdir')
if not os.path.exists('{0}/voldrv_vpools'.format(configuration_dir)):
    os.makedirs('{0}/voldrv_vpools'.format(configuration_dir))
for json_file in os.listdir('{0}/voldrv_vpools'.format(configuration_dir)):
    if json_file.endswith('.json'):
        storagedriver_config = StorageDriverConfiguration(json_file.replace('.json', ''))
        storagedriver_config.configure_arakoon_cluster(voldrv_arakoon_cluster_id, voldrv_arakoon_client_config)
"""
                    SetupController._exec_python(client_node, update_voldrv)

                for node in nodes:
                    node_client = SSHClient.load(node)
                    SetupController._configure_amqp_to_volumedriver(node_client)

                print 'Starting model services'
                logger.debug('Starting model services')
                for service in model_services:
                    for node in nodes:
                        node_client = SSHClient.load(node)
                        if SetupController._has_service(node_client, service):
                            SetupController._enable_service(node_client, service)
                            SetupController._change_service_state(node_client, service, 'start')

                if not join_cluster:
                    print 'Start model migration'
                    logger.debug('Start model migration')
                    from ovs.extensions.migration.migration import Migration
                    Migration.migrate()

                client = SSHClient.load(ip)
                logger.info('Update ES configuration')
                SetupController._update_es_configuration(client, 'true')

            else:
                print '\n+++ Adding extra node +++\n'
                logger.info('Adding extra node')
                for cluster in arakoon_clusters.keys():
                    master_client = SSHClient.load(master_ip)
                    client_config = SetupController._remote_config_read(master_client, arakoon_client_config.format(cluster))
                    target_client = SSHClient.load(ip)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/ovsdb', True)
                    target_client.dir_ensure('/opt/OpenvStorage/config/arakoon/voldrv', True)
                    SetupController._remote_config_write(target_client, arakoon_client_config.format(cluster), client_config)

                print 'Configuring services'
                logger.info('Copying client configurations')
                for config in generic_configfiles.keys():
                    master_client = SSHClient.load(master_ip)
                    client_config = SetupController._remote_config_read(master_client, config)
                    target_client = SSHClient.load(ip)
                    SetupController._remote_config_write(target_client, config, client_config)

            print '\n+++ Finalizing setup +++\n'
            logger.info('Finalizing setup')
            client = SSHClient.load(ip)
            client.run('mkdir -p /opt/OpenvStorage/webapps/frontend/logging')
            SetupController._change_service_state(client, 'logstash', 'restart')
            SetupController._replace_param_in_config(client,
                                                     '/opt/OpenvStorage/webapps/frontend/logging/config.js',
                                                     'http://"+window.location.hostname+":9200',
                                                     'http://' + cluster_ip + ':9200')

            # Imports, not earlier than here, as all required config files should be in place.
            from ovs.dal.hybrids.pmachine import PMachine
            from ovs.dal.lists.pmachinelist import PMachineList
            from ovs.dal.hybrids.storagerouter import StorageRouter
            from ovs.dal.lists.storagerouterlist import StorageRouterList

            print 'Configuring/updating model'
            logger.info('Configuring/updating model')
            pmachine = None
            for current_pmachine in PMachineList.get_pmachines():
                if current_pmachine.ip == hypervisor_ip and current_pmachine.hvtype == hypervisor_type:
                    pmachine = current_pmachine
                    break
            if pmachine is None:
                pmachine = PMachine()
                pmachine.ip = hypervisor_ip
                pmachine.username = hypervisor_username
                pmachine.password = hypervisor_password
                pmachine.hvtype = hypervisor_type
                pmachine.name = hypervisor_name
                pmachine.save()
            storagerouter = None
            for current_storagerouter in StorageRouterList.get_storagerouters():
                if current_storagerouter.ip == ip and current_storagerouter.machine_id == unique_id:
                    storagerouter = current_storagerouter
                    break
            if storagerouter is None:
                storagerouter = StorageRouter()
                storagerouter.name = node_name
                storagerouter.machine_id = unique_id
                storagerouter.ip = cluster_ip
            storagerouter.pmachine = pmachine
            storagerouter.save()

            print 'Updating configuration files'
            logger.info('Updating configuration files')
            ovs_config.set('grid', 'ip', cluster_ip)
            target_client = SSHClient.load(ip)
            SetupController._remote_config_write(target_client, '/opt/OpenvStorage/config/ovs.cfg', ovs_config)

            print 'Starting services'
            if join_masters is True:
                logger.info('Starting services for join master')
                for service in master_services:
                    for node in nodes:
                        node_client = SSHClient.load(node)
                        if SetupController._has_service(node_client, service):
                            SetupController._enable_service(node_client, service)
                            SetupController._change_service_state(node_client, service, 'start')
                # Enable HA for the rabbitMQ queues
                client = SSHClient.load(ip)
                output = client.run('sleep 5;rabbitmqctl set_policy ha-all "^(volumerouter|ovs_.*)$" \'{"ha-mode":"all"}\'', quiet=True)
                output = output.split('\r\n')
                retry = False
                for line in output:
                    if 'Error: unable to connect to node ' in line:
                        rabbitmq_running, rabbitmq_pid = SetupController._is_rabbitmq_running(client)
                        if rabbitmq_running and rabbitmq_pid:
                            client.run('kill {0}'.format(rabbitmq_pid), quiet=True)
                            print('Process killed, restarting')
                            client.run('service ovs-rabbitmq start', quiet=True)
                            retry = True
                            break
                if retry:
                    client.run('sleep 5;rabbitmqctl set_policy ha-all "^(volumerouter|ovs_.*)$" \'{"ha-mode":"all"}\'')

                rabbitmq_running, rabbitmq_pid, ovs_rabbitmq_running, same_process = SetupController._is_rabbitmq_running(client, True)
                if ovs_rabbitmq_running and same_process:
                    pass  # correct process is running
                elif rabbitmq_running and not ovs_rabbitmq_running:
                    # wrong process is running, must be stopped and correct one started
                    print('WARNING: an instance of rabbitmq-server is running, this needs to be stopped, ovs-rabbitmq will be started instead')
                    client.run('service rabbitmq-server stop', quiet=True)
                    time.sleep(5)
                    try:
                        client.run('kill {0}'.format(rabbitmq_pid), quiet=True)
                        print('Process killed')
                    except SystemExit:
                        print('Process already stopped')
                    client.run('service ovs-rabbitmq start', quiet=True)
                elif not rabbitmq_running and not ovs_rabbitmq_running:
                    #neither running
                    client.run('service ovs-rabbitmq start', quiet=True)

            target_client = SSHClient.load(ip)
            SetupController._enable_service(target_client, 'watcher')
            SetupController._change_service_state(target_client, 'watcher', 'start')

            logger.debug('Restarting workers')
            for node in nodes:
                node_client = SSHClient.load(node)
                SetupController._enable_service(node_client, 'workers')
                SetupController._change_service_state(node_client, 'workers', 'restart')

            print '\n+++ Announcing service +++\n'
            logger.info('Announcing service')
            target_client = SSHClient.load(ip)
            target_client.run("""cat > {3} <<EOF
<?xml version="1.0" standalone='no'?>
<!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- $Id$ -->
<service-group>
    <name replace-wildcards="yes">ovs_cluster_{0}_{1}</name>
    <service>
        <type>_ovs_{2}_node._tcp</type>
        <port>443</port>
    </service>
</service-group>
EOF
""".format(cluster_name, node_name, node_type, avahi_filename))
            SetupController._change_service_state(target_client, 'avahi-daemon', 'restart')

            print ''
            if join_cluster:
                print Interactive.boxed_message(['Setup complete.',
                                                 'Point your browser to http://{0} to use Open vStorage'.format(master_ip)])
            else:
                print Interactive.boxed_message(['Setup complete.',
                                                 'Point your browser to http://{0} to start using Open vStorage'.format(cluster_ip)])
            logger.info('Setup complete')

        except Exception as exception:
            print ''  # Spacing
            print Interactive.boxed_message(['An unexpected error occurred:', str(exception)])
            logger.exception('Unexpected error')
            logger.error(str(exception))
            sys.exit(1)

    @staticmethod
    def _add_service(client, name, params=None):
        if params is None:
            params = {}
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.add_service('', '{0}', '', '', {1})
"""
#        Service.add_service('', name, '', '', params)
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name, params))

    @staticmethod
    def _remove_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.remove_service('', '{0}')
"""
#        Service.remove_service('', name, '', '')
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _disable_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.disable_service('{0}')
"""
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _enable_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
Service.enable_service('{0}')
"""
        _ = SetupController._exec_python(client,
                                         run_service_script.format(name))

    @staticmethod
    def _has_service(client, name):
        has_service_script = """
from ovs.plugin.provider.service import Service
print Service.has_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              has_service_script.format(name))
        if status == 'True':
            return True
        return False

    @staticmethod
    def _get_service_status(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.get_service_status('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        if status == 'True':
            return True
        if status == 'False':
            return False
        return None

    @staticmethod
    def _restart_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.restart_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _start_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.start_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _stop_service(client, name):
        run_service_script = """
from ovs.plugin.provider.service import Service
print Service.stop_service('{0}')
"""
        status = SetupController._exec_python(client,
                                              run_service_script.format(name))
        return status

    @staticmethod
    def _configure_arakoon(configs, uid, cluster, ip, port, target_client, arakoon_configs, arakoon_mountpoint):
        arakoon_client_config, arakoon_server_config = arakoon_configs
        client_config, server_config = configs

        if not client_config.has_section('global'):
            client_config.add_section('global')
        client_config.set('global', 'cluster_id', cluster)
        current_cluster = list()
        if client_config.has_option('global', 'cluster'):
            current_cluster = [n.strip() for n in client_config.get('global', 'cluster').split(',')]
        if uid not in current_cluster:
            current_cluster.append(uid)
        client_config.set('global', 'cluster', ', '.join(current_cluster))
        if not client_config.has_section(uid):
            client_config.add_section(uid)
            client_config.set(uid, 'name', uid)
            client_config.set(uid, 'ip', ip)
            client_config.set(uid, 'client_port', port)
        SetupController._remote_config_write(target_client, arakoon_client_config.format(cluster), client_config)

        if not server_config.has_section('global'):
            server_config.add_section('global')
        server_config.set('global', 'cluster_id', cluster)
        current_cluster = list()
        if server_config.has_option('global', 'cluster'):
            current_cluster = [n.strip() for n in server_config.get('global', 'cluster').split(',')]
        if uid not in current_cluster:
            current_cluster.append(uid)
        server_config.set('global', 'cluster', ', '.join(current_cluster))
        if not server_config.has_section(uid):
            server_config.add_section(uid)
            server_config.set(uid, 'name', uid)
            server_config.set(uid, 'ip', ip)
            server_config.set(uid, 'client_port', port)
            server_config.set(uid, 'messaging_port', port + 1)
            server_config.set(uid, 'log_level', 'info')
            server_config.set(uid, 'log_dir', '/var/log/arakoon/{0}'.format(cluster))
            server_config.set(uid, 'home', '{0}/arakoon/{1}'.format(arakoon_mountpoint, cluster))
            server_config.set(uid, 'tlog_dir', '{0}/tlogs/{1}'.format(arakoon_mountpoint, cluster))
            server_config.set(uid, 'fsync', 'true')
        SetupController._remote_config_write(target_client, arakoon_server_config.format(cluster), server_config)

    @staticmethod
    def _get_disk_configuration(client):
        """
        Connect to target host and retrieve sata/ssd/raid configuration
        """

        remote_script = """
from string import digits
import pyudev
import glob
import re
import os

blk_patterns = ['sd.*', 'fio.*', 'vd.*', 'xvd.*']
blk_devices = dict()

def get_boot_device():
    mtab = open('/etc/mtab').read().splitlines()
    for line in mtab:
        if ' / ' in line:
            boot_partition = line.split()[0]
            return boot_partition.lstrip('/dev/').translate(None, digits)

boot_device = get_boot_device()

def get_value(device, property):
    return str(open('/sys/block/' + device + '/' + property).read())

def get_size_in_bytes(device):
    sectors = get_value(device, 'size')
    sector_size = get_value(device, 'queue/hw_sector_size')
    return float(sectors) * float(sector_size)

def get_device_type(device):
    '''
    determine ssd or disk = accurate
    determine ssd == accelerator = best guess

    Returns: disk|ssd|accelerator|unknown
    '''

    rotational = get_value(device, 'queue/rotational')
    if '1' in str(rotational):
        return 'disk'
    else:
        return 'ssd'

def is_part_of_sw_raid(device):
    #ID_FS_TYPE linux_raid_member
    #ID_FS_USAGE raid

    context = pyudev.Context()
    devices = context.list_devices(subsystem='block')
    is_raid_member = False

    for entry in devices:
        if device not in entry['DEVNAME']:
            continue

        if entry['DEVTYPE']=='partition' and 'ID_FS_USAGE' in entry.keys():
            if 'raid' in entry['ID_FS_USAGE'].lower():
                is_raid_member = True

    return is_raid_member

def get_drive_model(device):
    context = pyudev.Context()
    devices = context.list_devices(subsystem='block')

    for entry in devices:
        if device not in entry['DEVNAME']:
            continue

        if entry['DEVTYPE']=='disk' and 'ID_MODEL' in entry.keys():
            return str(entry['ID_MODEL'])

    if 'fio' in device:
        return 'FUSIONIO'

    return ''

def get_device_details(device):
    return {'size' : get_size_in_bytes(device),
            'type' : get_device_type(device),
            'software_raid' : is_part_of_sw_raid(device),
            'model' : get_drive_model(device),
            'boot_device' : device == boot_device
           }

for device_path in glob.glob('/sys/block/*'):
    device = os.path.basename(device_path)
    for pattern in blk_patterns:
        if re.compile(pattern).match(device):
            blk_devices[device] = get_device_details(device)


print blk_devices
"""

        blk_devices = eval(SetupController._exec_python(client, remote_script))

        # cross-check ssd devices - flawed detection on vmware
        for disk in blk_devices.keys():
            output = str(client.run("hdparm -I {0} 2> /dev/null | grep 'Solid State' || true".format('/dev/' + disk)).strip())
            if 'Solid State' in output and blk_devices[disk]['type'] == 'disk':
                print 'Updating device type for /dev/{0} to ssd'.format(disk)
                blk_devices[disk]['type'] = 'ssd'

        return blk_devices

    @staticmethod
    def _generate_default_partition_layout(blk_devices):
        """
        Process detected block devices while
        - ignoring bootdevice unless it's the only one
        - ignoring devices part of a software raid

        """

        mountpoints_to_allocate = {'/mnt/md': {'device': 'DIR_ONLY', 'percentage': 'NA', 'label': 'mdpath'},
                                   '/mnt/db': {'device': 'DIR_ONLY', 'percentage': 'NA', 'label': 'db'},
                                   '/mnt/cache1': dict(SetupController.PARTITION_DEFAULTS),
                                   '/mnt/bfs': {'device': 'DIR_ONLY', 'percentage': 'NA', 'label': 'backendfs'},
                                   '/var/tmp': {'device': 'DIR_ONLY', 'percentage': 'NA', 'label': 'tempfs'}}

        selected_devices = dict(blk_devices)
        skipped_devices = set()
        for device, values in blk_devices.iteritems():
            if values['boot_device']:
                skipped_devices.add(device)
            if values['software_raid']:
                skipped_devices.add(device)

        for device in skipped_devices:
            selected_devices.pop(device)

        ssd_devices = list()
        disk_devices = list()

        for device, values in selected_devices.iteritems():
            if values['type'] == 'ssd':
                ssd_devices.append('/dev/' + device)
            if values['type'] == 'disk':
                disk_devices.append('/dev/' + device)

        nr_of_ssds = len(ssd_devices)
        nr_of_disks = len(disk_devices)

        print '{0} ssd devices: {1}'.format(nr_of_ssds, str(ssd_devices))
        print '{0} sata drives: {1}'.format(nr_of_disks, str(disk_devices))
        print

        if nr_of_disks == 1:
            mountpoints_to_allocate['/var/tmp']['device'] = disk_devices[0]
            mountpoints_to_allocate['/var/tmp']['percentage'] = 20
            mountpoints_to_allocate['/mnt/bfs']['device'] = disk_devices[0]
            mountpoints_to_allocate['/mnt/bfs']['percentage'] = 80

        elif nr_of_disks >= 2:
            mountpoints_to_allocate['/var/tmp']['device'] = disk_devices[0]
            mountpoints_to_allocate['/var/tmp']['percentage'] = 100
            mountpoints_to_allocate['/mnt/bfs']['device'] = disk_devices[1]
            mountpoints_to_allocate['/mnt/bfs']['percentage'] = 100

        if nr_of_ssds == 1:
            mountpoints_to_allocate['/mnt/cache1']['device'] = ssd_devices[0]
            mountpoints_to_allocate['/mnt/cache1']['percentage'] = 50
            mountpoints_to_allocate['/mnt/md']['device'] = ssd_devices[0]
            mountpoints_to_allocate['/mnt/md']['percentage'] = 25
            mountpoints_to_allocate['/mnt/db']['device'] = ssd_devices[0]
            mountpoints_to_allocate['/mnt/db']['percentage'] = 25

        elif nr_of_ssds >= 2:
            for count in xrange(nr_of_ssds):
                marker = str('/mnt/cache' + str(count + 1))
                mountpoints_to_allocate[marker] = dict(SetupController.PARTITION_DEFAULTS)
                mountpoints_to_allocate[marker]['device'] = ssd_devices[count]
                mountpoints_to_allocate[marker]['label'] = 'cache' + str(count + 1)
                if count < 2:
                    cache_size = 75
                else:
                    cache_size = 100
                mountpoints_to_allocate[marker]['percentage'] = cache_size

            mountpoints_to_allocate['/mnt/md']['device'] = ssd_devices[0]
            mountpoints_to_allocate['/mnt/md']['percentage'] = 25
            mountpoints_to_allocate['/mnt/db']['device'] = ssd_devices[1]
            mountpoints_to_allocate['/mnt/db']['percentage'] = 25

        return mountpoints_to_allocate, skipped_devices

    @staticmethod
    def _partition_disks(client, partition_layout):
        fstab_entry = 'LABEL={0}    {1}         ext4    defaults,nobootwait,noatime,discard    0    2 \n'
        mounted = [device.strip() for device in client.run("cat /etc/mtab | cut -d ' ' -f 2").strip().split('\n')]

        unique_disks = set()
        for mp, values in partition_layout.iteritems():
            unique_disks.add(values['device'])

            # umount partitions
            if mp in mounted:
                print 'Unmounting {0}'.format(mp)
                client.run('umount {0}'.format(mp))

        # wipe disks
        for disk in unique_disks:
            if disk == 'DIR_ONLY':
                continue
            client.run('parted {0} -s mklabel gpt'.format(disk))

        # pre process partition info (disk as key)
        mountpoints = partition_layout.keys()
        mountpoints.sort()
        partitions_by_disk = dict()
        for mp in mountpoints:
            partition = partition_layout[mp]
            disk = partition['device']
            percentage = partition['percentage']
            label = partition['label']
            if disk in partitions_by_disk:
                partitions_by_disk[disk].append((mp, percentage, label))
            else:
                partitions_by_disk[disk] = [(mp, percentage, label)]

        # partition and format disks
        fstab = '# BEGIN Open vStorage \n'
        for disk, partitions in partitions_by_disk.iteritems():
            if disk == 'DIR_ONLY':
                for directory, _, _ in partitions:
                    client.run('mkdir -p {0}'.format(directory))
                continue

            start = '2MB'
            count = 1
            for mp, percentage, label in partitions:
                if start == '2MB':
                    size_in_percentage = int(percentage)
                    client.run('parted {0} -s mkpart {1} {2} {3}%'.format(disk, label, start, size_in_percentage))
                else:
                    size_in_percentage = int(start) + int(percentage)
                    client.run('parted {0} -s mkpart {1} {2}% {3}%'.format(disk, label, start, size_in_percentage))
                client.run('mkfs.ext4 -q {0} -L {1}'.format(disk + str(count), label))
                fstab = fstab + fstab_entry.format(label, mp)
                count += 1
                start = size_in_percentage

        fstab += '# END OPENVSTORAGE \n'

        # update fstab
        must_update = False
        fstab_content = client.file_read('/etc/fstab')
        if not '# BEGIN Open vStorage' in fstab_content:
            fstab_content += '\n'
            fstab_content += fstab
            must_update = True
        if must_update:
            client.file_write('/etc/fstab', fstab_content)

        try:
            client.run('timeout -k 9 5s mountall -q || true')
        except:
            pass  # The above might fail sometimes. We don't mind and will try again
        client.run('swapoff --all')
        client.run('mountall -q')

    @staticmethod
    def apply_flexible_disk_layout(client, auto_config=False, default=dict()):
        import choice
        blk_devices = SetupController._get_disk_configuration(client)

        skipped = set()
        if not default:
            default, skipped = SetupController._generate_default_partition_layout(blk_devices)

        print 'Excluded: {0}'.format(skipped)
        print '-> bootdisk or part of software RAID configuration'
        print

        device_size_map = dict()
        for key, values in blk_devices.iteritems():
            device_size_map['/dev/' + key] = values['size']

        def show_layout(proposed):
            print 'Proposed partition layout:'
            keys = proposed.keys()
            keys.sort()
            key_map = list()
            for mp in keys:
                sub_keys = proposed[mp].keys()
                sub_keys.sort()
                mp_values = ''
                if not proposed[mp]['device'] or proposed[mp]['device'] in ['DIR_ONLY']:
                    mp_values = ' {0} : {1:20}'.format('device', 'DIR_ONLY')
                    print "{0:20} : {1}".format(mp, mp_values)
                    key_map.append(mp)
                    continue

                for sub_key in sub_keys:
                    value = str(proposed[mp][sub_key])
                    if sub_key == 'device' and value and value != 'DIR_ONLY':
                        size = device_size_map[value]
                        size_in_gb = int(size / 1000.0 / 1000.0 / 1000.0)
                        value = value + ' ({0} GB)'.format(size_in_gb)
                    if sub_key in ['device']:
                        mp_values = mp_values + ' {0} : {1:20}'.format(sub_key, value)
                    elif sub_key in ['label']:
                        mp_values = mp_values + ' {0} : {1:10}'.format(sub_key, value)
                    else:
                        mp_values = mp_values + ' {0} : {1:5}'.format(sub_key, value)

                print "{0:20} : {1}".format(mp, mp_values)
                key_map.append(mp)
            print

            return key_map

        def show_submenu_layout(subitem, mountpoint):
            sub_keys = subitem.keys()
            sub_keys.sort()
            for sub_key in sub_keys:
                print "{0:15} : {1}".format(sub_key, subitem[sub_key])
            print "{0:15} : {1}".format('mountpoint', mountpoint)
            print

        def is_device_path(value):
            if re.match('/dev/[a-z][a-z][a-z]+', value):
                return True
            else:
                return False

        def is_mountpoint(value):
            if re.match('/mnt/[a-z]+[0-9]*', value):
                return True
            else:
                return False

        def is_percentage(value):
            try:
                if 0 <= int(value) <= 100:
                    return True
                else:
                    return False
            except ValueError:
                return False

        def is_label(value):
            if re.match('[a-z]+[0-9]*', value):
                return True
            else:
                return False

        def validate_subitem(subitem, answer):
            if subitem in ['percentage']:
                return is_percentage(answer)
            elif subitem in ['device']:
                return is_device_path(answer)
            elif subitem in ['mountpoint']:
                return is_mountpoint(answer)
            elif subitem in ['label']:
                return is_label(answer)

            return False

        def _summarize_partition_percentages(layout):
            total = dict()
            for details in layout.itervalues():
                device = details['device']
                if device == 'DIR_ONLY':
                    continue
                if details['percentage'] == 'NA':
                    print '>>> Invalid value {0}% for device: {1}'.format(details['percentage'], device)
                    time.sleep(1)
                    return False
                percentage = int(details['percentage'])
                if device in total:
                    total[device] += percentage
                else:
                    total[device] = percentage
            print total
            for device, percentage in total.iteritems():
                if is_percentage(percentage):
                    continue
                else:
                    print '>>> Invalid total {0}% for device: {1}'.format(percentage, device)
                    time.sleep(1)
                    return False
            return True

        def process_submenu_actions(mp_to_edit):
            subitem = default[mp_to_edit]
            submenu_items = subitem.keys()
            submenu_items.sort()
            submenu_items.append('mountpoint')
            submenu_items.append('finish')

            print 'Mountpoint: {0}'.format(mp_to_edit)
            while True:
                show_submenu_layout(subitem, mp_to_edit)
                subitem_chosen = choice.Menu(submenu_items).ask()
                if subitem_chosen == 'finish':
                    break
                elif subitem_chosen == 'mountpoint':
                    new_mountpoint = choice.Input('Enter new mountpoint: ', str).ask()
                    if new_mountpoint in default:
                        print 'New mountpoint already exists!'
                    else:
                        mp_values = default[mp_to_edit]
                        default.pop(mp_to_edit)
                        default[new_mountpoint] = mp_values
                        mp_to_edit = new_mountpoint
                else:
                    answer = choice.Input('Enter new value for {}'.format(subitem_chosen)).ask()
                    if validate_subitem(subitem_chosen, answer):
                        subitem[subitem_chosen] = answer
                    else:
                        print '\n>>> Invalid entry {0} for {1}\n'.format(answer, subitem_chosen)
                        time.sleep(1)

        if auto_config:
            SetupController._partition_disks(client, default)
            return default

        else:
            choices = show_layout(default)
            while True:
                menu_actions = ['Add', 'Remove', 'Update', 'Print', 'Apply', 'Quit']
                menu_devices = list(choices)
                menu_devices.sort()
                chosen = choice.Menu(menu_actions).ask()

                if chosen == 'Add':
                    to_add = choice.Input('Enter mountpoint to add:', str).ask()
                    if to_add in default:
                        print 'Mountpoint {0} already exists'.format(to_add)
                    else:
                        default[to_add] = dict(SetupController.PARTITION_DEFAULTS)
                    choices = show_layout(default)

                elif chosen == 'Remove':
                    to_remove = choice.Input('Enter mountpoint to remove:', str).ask()
                    if to_remove in default:
                        default.pop(to_remove)
                    else:
                        print 'Mountpoint {0} not found, no action taken'.format(to_remove)
                    choices = show_layout(default)

                elif chosen == 'Update':
                    print 'Choose mountpoint to update:'
                    to_update = choice.Menu(menu_devices).ask()
                    process_submenu_actions(to_update)
                    choices = show_layout(default)

                elif chosen == 'Print':
                    show_layout(default)

                elif chosen == 'Apply':
                    if not _summarize_partition_percentages(default):
                        'Partition totals are not within percentage range'
                        choices = show_layout(default)
                        continue

                    show_layout(default)
                    confirmation = choice.Input('Please confirm partition layout (yes/no), ALL DATA WILL BE ERASED ON THE DISKS ABOVE!', str).ask()
                    if confirmation.lower() == 'yes':
                        print 'Applying partition layout ...'
                        SetupController._partition_disks(client, default)
                        return default
                    else:
                        print 'Please confirm by typing yes'
                elif chosen == 'Quit':
                    return 'QUIT'

    @staticmethod
    def _discover_nodes(client):
        nodes = {}
        SetupController._change_service_state(client, 'dbus', 'start')
        SetupController._change_service_state(client, 'avahi-daemon', 'start')
        discover_result = client.run('avahi-browse -artp 2> /dev/null | grep ovs_cluster || true')
        for entry in discover_result.split('\n'):
            entry_parts = entry.split(';')
            if entry_parts[0] == '=' and entry_parts[2] == 'IPv4':
                # =;eth0;IPv4;ovs_cluster_kenneth_ovs100;_ovs_master_node._tcp;local;ovs100.local;172.22.1.10;443;
                # split(';') -> [3]  = ovs_cluster_kenneth_ovs100
                #               [4]  = _ovs_master_node._tcp -> contains _ovs_<type>_node
                #               [7]  = 172.22.1.10 (ip)
                # split('_') -> [-1] = ovs100 (node name)
                #               [-2] = kenneth (cluster name)
                cluster_info = entry_parts[3].split('_')
                cluster_name = cluster_info[-2]
                node_name = cluster_info[-1]
                if cluster_name not in nodes:
                    nodes[cluster_name] = {}
                if node_name not in nodes[cluster_name]:
                    nodes[cluster_name][node_name] = {}
                nodes[cluster_name][node_name]['ip'] = entry_parts[7]
                nodes[cluster_name][node_name]['type'] = entry_parts[4].split('_')[2]
        return nodes

    @staticmethod
    def _validate_ip(ip):
        regex = '^(((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))$'
        match = re.search(regex, ip)
        return match is not None

    @staticmethod
    def _discover_hypervisor(client):
        hypervisor = None
        module = client.run('lsmod | grep kvm || true').strip()
        if module != '':
            hypervisor = 'KVM'
        else:
            disktypes = client.run('dmesg | grep VMware || true').strip()
            if disktypes != '':
                hypervisor = 'VMWARE'
        return hypervisor

    @staticmethod
    def _remote_config_read(client, filename):
        contents = client.file_read(filename)
        with open('/tmp/temp_read.cfg', 'w') as configfile:
            configfile.write(contents)
        config = ConfigParser.ConfigParser()
        config.read('/tmp/temp_read.cfg')
        return config

    @staticmethod
    def _remote_config_write(client, filename, config):
        with open('/tmp/temp_write.cfg', 'w') as configfile:
            config.write(configfile)
        with open('/tmp/temp_write.cfg', 'r') as configfile:
            contents = configfile.read()
            client.file_write(filename, contents)

    @staticmethod
    def _replace_param_in_config(client, config_file, old_value, new_value, add=False):
        if client.file_exists(config_file):
            contents = client.file_read(config_file)
            if new_value in contents and new_value.find(old_value) > 0:
                pass
            elif old_value in contents:
                contents = contents.replace(old_value, new_value)
            elif add:
                contents += new_value + '\n'
            client.file_write(config_file, contents)

    @staticmethod
    def _exec_python(client, script):
        """
        Executes a python script on the client
        """
        return client.run('python -c """{0}"""'.format(script))

    @staticmethod
    def _change_service_state(client, name, state):
        """
        Starts/stops/restarts a service
        """

        action = None
        status = SetupController._get_service_status(client, name)
        if status is False and state in ['start', 'restart']:
            SetupController._start_service(client, name)
            action = 'started'
        elif status is True and state == 'stop':
            SetupController._stop_service(client, name)
            action = 'stopped'
        elif status is True and state == 'restart':
            SetupController._restart_service(client, name)
            action = 'restarted'

        if action is None:
            print '  [{0}] {1} already {2}'.format(client.ip, name, 'running' if status is True else 'halted')
        else:
            timeout = 300
            safetycounter = 0
            while safetycounter < timeout:
                status = SetupController._get_service_status(client, name)
                if (status is False and state == 'stop') or (status is True and state in ['start', 'restart']):
                    break
                safetycounter += 1
                time.sleep(1)
            if safetycounter == timeout:
                raise RuntimeError('Service {0} could not be {1} on node {2}'.format(name, action, client.ip))
            print '  [{0}] {1} {2}'.format(client.ip, name, action)

    @staticmethod
    def _update_es_configuration(es_client, value):
        # update elasticsearch configuration
        config_file = '/etc/elasticsearch/elasticsearch.yml'
        SetupController._change_service_state(es_client, 'elasticsearch', 'stop')
        SetupController._replace_param_in_config(es_client,
                                                 config_file,
                                                 '<IS_POTENTIAL_MASTER>',
                                                 value)
        SetupController._replace_param_in_config(es_client,
                                                 config_file,
                                                 '<IS_DATASTORE>',
                                                 value)
        SetupController._change_service_state(es_client, 'elasticsearch', 'start')
        SetupController._change_service_state(es_client, 'logstash', 'restart')

    @staticmethod
    def _configure_amqp_to_volumedriver(client, vpname=None):
        """
        Reads out the RabbitMQ client config, using that to (re)configure the volumedriver configuration(s)
        """
        remote_script = """
import os
import ConfigParser
from ovs.plugin.provider.configuration import Configuration
protocol = Configuration.get('ovs.core.broker.protocol')
login = Configuration.get('ovs.core.broker.login')
password = Configuration.get('ovs.core.broker.password')
vpool_name = {0}
uris = []
cfg = ConfigParser.ConfigParser()
cfg.read('/opt/OpenvStorage/config/rabbitmqclient.cfg')
nodes = [n.strip() for n in cfg.get('main', 'nodes').split(',')]
for node in nodes:
    uris.append({{'amqp_uri': '{{0}}://{{1}}:{{2}}@{{3}}'.format(protocol, login, password, cfg.get(node, 'location'))}})
from ovs.extensions.storageserver.storagedriver import StorageDriverConfiguration
queue_config = {{'events_amqp_routing_key': Configuration.get('ovs.core.broker.volumerouter.queue'),
                 'events_amqp_uris': uris}}
for config_file in os.listdir('/opt/OpenvStorage/config/voldrv_vpools'):
    this_vpool_name = config_file.replace('.json', '')
    if config_file.endswith('.json') and (vpool_name is None or vpool_name == this_vpool_name):
        storagedriver_configuration = StorageDriverConfiguration(this_vpool_name)
        storagedriver_configuration.configure_event_publisher(queue_config)"""
        SetupController._exec_python(client, remote_script.format(vpname if vpname is None else "'{0}'".format(vpname)))

    @staticmethod
    def _is_rabbitmq_running(client, check_ovs=False):
        rabbitmq_running, rabbitmq_pid = False, 0
        ovs_rabbitmq_running, pid = False, -1
        output = client.run('service rabbitmq-server status', quiet=True)
        if 'unrecognized service' in output:
            output = None
        if output:
            output = output.split('\r\n')
            for line in output:
                if 'pid' in line:
                    rabbitmq_running = True
                    rabbitmq_pid = line.split(',')[1].replace('}', '')
        else:
            output = client.run('ps aux | grep rabbit@ | grep -v grep', quiet=True)
            if output:  # in case of error it is ''
                output = output.split(' ')
                if output[0] == 'rabbitmq':
                    rabbitmq_pid = output[1]
                    for item in output[2:]:
                        if 'erlang' in item or 'rabbitmq' in item or 'beam' in item:
                            rabbitmq_running = True
        output = client.run('service ovs-rabbitmq status', quiet=True)
        if 'stop/waiting' in output:
            pass
        if 'start/running' in output:
            pid = output.split('process ')[1].strip()
            ovs_rabbitmq_running = True
        same_process = rabbitmq_pid == pid
        if check_ovs:
            return rabbitmq_running, rabbitmq_pid, ovs_rabbitmq_running, same_process
        return rabbitmq_running, rabbitmq_pid
