// Copyright (C) 2016 iNuron NV
//
// This file is part of Open vStorage Open Source Edition (OSE),
// as available from
//
//      http://www.openvstorage.org and
//      http://www.openvstorage.com.
//
// This file is free software; you can redistribute it and/or modify it
// under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
// as published by the Free Software Foundation, in version 3 as it comes
// in the LICENSE.txt file of the Open vStorage OSE distribution.
//
// Open vStorage is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY of any kind.
/*global define */
define([
    'jquery', 'durandal/app', 'plugins/dialog', 'knockout', 'plugins/router',
    'ovs/shared', 'ovs/generic', 'ovs/refresher', 'ovs/plugins/pluginloader',
    'ovs/services/authentication',
    'viewmodels/containers/vdisk/vdisk', 'viewmodels/containers/vpool/vpool',
    'viewmodels/containers/storagerouter/storagerouter', 'viewmodels/containers/domain/domain',
    'viewmodels/wizards/clone/index', 'viewmodels/wizards/vdiskmove/index', 'viewmodels/wizards/rollback/index', 'viewmodels/wizards/snapshot/index',
    './vdisk-detail-data',
    'viewmodels/services/domain', 'viewmodels/services/storagerouter', 'viewmodels/services/vdisk'
], function(
    $, app, dialog, ko, router,
    shared, generic, Refresher, pluginLoader,
    authentication,
    VDisk, VPool, StorageRouter, Domain,
    CloneWizard, MoveWizard, RollbackWizard, SnapshotWizard,
    vdiskDetailData,
    domainService, storagerouterService, vDiskService
) {
    "use strict";
    return function() {
        var self = this;

        // Variables
        self.domainCache      = {};
        self.shared           = shared;
        self.guard            = { authenticated: true };
        self.refresher        = new Refresher();
        self.widgets          = [];
        self.snapshotHeaders  = [
            { key: 'label',         value: $.t('ovs:generic.description'), width: undefined },
            { key: 'timestamp',     value: $.t('ovs:generic.datetime'),    width: 200       },
            { key: 'stored',        value: $.t('ovs:generic.storeddata'),  width: 110       },
            { key: 'is_automatic',  value: $.t('ovs:generic.type'),        width: 110       },
            { key: 'is_consistent', value: $.t('ovs:generic.consistent'),  width: 100       },
            { key: 'is_sticky',     value: $.t('ovs:generic.sticky'),      width: 100       },
            { key: undefined,       value: $.t('ovs:generic.actions'),     width: 60        }
        ];
        self.edgeClientHeaders = [
            { key: 'clientIp',   value: $.t('ovs:vdisks.detail.client_ip'),   width: 120       },
            { key: 'clientPort', value: $.t('ovs:vdisks.detail.client_port'), width: 100       },
            { key: 'serverIp',   value: $.t('ovs:vdisks.detail.server_ip'),   width: 150       },
            { key: 'serverPort', value: $.t('ovs:vdisks.detail.server_port'), width: undefined }
        ];
        self.vDisk                = {};
        self.vDiskString          = 'vdisk-detail';


        // Observables
        self.convertingToTemplate = ko.observable(false);
        self.domains              = ko.observableArray([]);
        self.snapshotsInitialLoad = ko.observable(true);
        self.removing             = ko.observable(false);
        self.restarting           = ko.observable(false);

        // Handles
        self.loadDomainHandle        = undefined;
        self.loadStorageRouterHandle = undefined;

        // Functions
        self.load = function() {
            return self.vDisk.load()
                .then(function() {
                    if (self.vDisk.isVTemplate()) {
                        router.navigateBack();
                        throw new Error('vDisk is a template. Not loading')
                    }
                })
                .then(self.loadStorageRouters)
                .then(self.loadDomains)
                .then(function() {
                    self.snapshotsInitialLoad(false);
                    var sr, pool, vdisk = self.vDisk,
                        storageRouterGuid = vdisk.storageRouterGuid(),
                        vPoolGuid = vdisk.vpoolGuid();
                    if (storageRouterGuid && (vdisk.storageRouter() === undefined || vdisk.storageRouter().guid() !== storageRouterGuid)) {
                        sr = new StorageRouter(storageRouterGuid);
                        sr.load('features');
                        vdisk.storageRouter(sr);
                    }
                    if (vPoolGuid && (vdisk.vpool() === undefined || vdisk.vpool().guid() !== vPoolGuid)) {
                        pool = new VPool(vPoolGuid);
                        pool.load('configuration');
                        vdisk.vpool(pool);
                    }
                }, function(error) {
                    if (error !== undefined && error.status === 404) {
                        router.navigateBack();
                    }
                    throw error
                })
        };
        self.loadDomains = function() {
            return $.Deferred(function(deferred) {
                var vdisk = self.vDisk;
                if (vdisk !== undefined && generic.xhrCompleted(self.loadDomainHandle)) {
                    self.loadDomainHandle = domainService.loadDomains({
                        sort: 'name',
                        contents: 'storage_router_layout',
                        vdisk_guid: vdisk.guid()
                    })
                        .done(function(data) {
                            var guids = [], ddata = {}, domainsPresent = false;
                            $.each(data.data, function(index, item) {
                                if (item.storage_router_layout.regular.contains(self.vDisk.storageRouterGuid())) {
                                    domainsPresent = true;
                                    if (item.storage_router_layout.regular.length > 1) {
                                        guids.push(item.guid);
                                        ddata[item.guid] = item;
                                    }
                                } else if (item.storage_router_layout.regular.length > 0)  {
                                    domainsPresent = true;
                                    guids.push(item.guid);
                                    ddata[item.guid] = item;
                                }
                            });

                            self.vDisk.domainsPresent(domainsPresent);
                            generic.crossFiller(
                                guids, self.domains,
                                function(guid) {
                                    var domain = new Domain(guid);
                                    if (!self.domainCache.hasOwnProperty(guid)) {
                                        self.domainCache[guid] = domain;
                                    }
                                    return domain;
                                }, 'guid'
                            );
                            $.each(self.domains(), function(index, domain) {
                                if (ddata.hasOwnProperty(domain.guid())) {
                                    domain.fillData(ddata[domain.guid()]);
                                }
                            });
                            self.vDisk.dtlTargets(guids);
                            deferred.resolve();
                        })
                        .fail(deferred.reject);
                } else {
                    deferred.resolve();
                }
            }).promise();
        };
        self.loadStorageRouters = function() {
            $.when().then (function () {
                if (generic.xhrCompleted(self.loadStorageRouterHandle)) {
                    self.loadStorageRouterHandle = storagerouterService.loadStorageRouters()
                        .done(function (data) {
                            self.vDisk.storageRouterGuids(data.data);
                        })
                }
            });
        };
        self.refreshSnapshots = function() {
            // Not in use, for mapping only
        };
        self.formatBytes = function(value) {
            return generic.formatBytes(value);
        };
        self.rollback = function() {
            if (self.vDisk !== undefined) {
                dialog.show(new RollbackWizard({
                    modal: true,
                    guid: self.vDisk.guid()
                }));
            }
        };
        self.snapshot = function() {
            if (self.vDisk !== undefined) {
                dialog.show(new SnapshotWizard({
                    modal: true,
                    guid: self.vDisk.guid()
                }));
            }
        };
        self.setAsTemplate = function() {
            if (self.canBeTemplated()) {
                var vd = self.vDisk;
                self.convertingToTemplate(true);
                app.showMessage(
                        $.t('ovs:vdisks.set_as_template.warning'),
                        $.t('ovs:vdisks.set_as_template.title', {what: vd.name()}),
                        [$.t('ovs:vdisks.set_as_template.no'), $.t('ovs:vdisks.set_as_template.yes')]
                    )
                    .done(function(answer) {
                        if (answer === $.t('ovs:vdisks.set_as_template.yes')) {
                            generic.alertInfo(
                                $.t('ovs:vdisks.set_as_template.started'),
                                $.t('ovs:vdisks.set_as_template.started_msg', {what: vd.name()})
                            );
                            vDiskService.setAsTemplate(vd.guid())
                                .done(function() {
                                    generic.alertSuccess(
                                        $.t('ovs:vdisks.set_as_template.success'),
                                        $.t('ovs:vdisks.set_as_template.success_msg', {what: vd.name()})
                                    );
                                    router.navigate(shared.routing.loadHash('vtemplates'));
                                })
                                .fail(function(error) {
                                    error = generic.extractErrorMessage(error);
                                    generic.alertError(
                                        $.t('ovs:generic.error'),
                                        $.t('ovs:vdisks.set_as_template.failed_msg', {what: vd.name(), why: error})
                                    );
                                })
                                .always(function() {
                                    self.convertingToTemplate(false);
                                });
                        } else {
                            self.convertingToTemplate(false);
                        }
                    });
            }
        };
        self.clone = function() {
            if (self.vDisk !== undefined) {
                dialog.show(new CloneWizard({
                    modal: true,
                    vdisk: self.vDisk
                }));
            }
        };
        self.move = function() {
            if (self.vDisk !== undefined) {
                dialog.show(new MoveWizard({
                    modal: true,
                    vdisk: self.vDisk
                }));
            }
        };
        self.scrub = function() {
            if (self.vDisk !== undefined) {
                var vd = self.vDisk;
                app.showMessage(
                        $.t('ovs:vdisks.scrub.title_message', {vdisk: vd.name()}),
                        $.t('ovs:vdisks.scrub.title', {vdisk: vd.name()}),
                        [$.t('ovs:generic.no'), $.t('ovs:generic.yes')]
                    )
                    .done(function(answer) {
                        if (answer === $.t('ovs:generic.yes')) {
                            generic.alertInfo(
                                $.t('ovs:vdisks.scrub.started_title'),
                                $.t('ovs:vdisks.scrub.started_message', {vdisk: vd.name()})
                            );
                            vDiskService.scrub(vd.guid())
                                .done(function() {
                                    generic.alertSuccess(
                                        $.t('ovs:vdisks.scrub.success_title'),
                                        $.t('ovs:vdisks.scrub.success_message', {vdisk: vd.name()})
                                    );
                                })
                                .fail(function(error) {
                                    error = generic.extractErrorMessage(error);
                                    generic.alertError(
                                        $.t('ovs:vdisks.scrub.failed_title'),
                                        $.t('ovs:vdisks.scrub.failed_message', {vdisk: vd.name(), why: error})
                                    );
                                });
                        }
                    });
            }
        };
        self.saveConfiguration = function() {
            if (self.vDisk !== undefined) {
                var vd = self.vDisk, new_config = $.extend({}, vd.configuration());
                if (!isNaN(new_config.cache_quota)) {
                    new_config.cache_quota *= Math.pow(1024, 3);
                } else {
                    // Update current configuration to default value stored in vPool, otherwise 'Save' button will be enabled after saving
                    var quota = vd.vpool().metadata().caching_info[self.vDisk.storageRouterGuid()].quota;
                    vd.configuration().cache_quota = quota / Math.pow(1024.0, 3);
                }
                vDiskService.setConfig(vd.guid(), new_config)
                    .done(function () {
                        generic.alertSuccess(
                            $.t('ovs:vdisks.save_config.success'),
                            $.t('ovs:vdisks.save_config.success_msg', {what: vd.name()})
                        );
                    })
                    .fail(function (error) {
                        error = generic.extractErrorMessage(error);
                        generic.alertError(
                            $.t('ovs:generic.error'),
                            $.t('ovs:vdisks.save_config.failed_msg', {what: vd.name(), why: error})
                        );
                    })
                    .always(function() {
                        vd.loadConfiguration(false);
                    });
                vd.oldConfiguration($.extend({}, vd.configuration()));
                vd.dtlTarget(vd.configuration().dtl_target.slice());
            }
        };
        self.removeSnapshot = function(snapshotid) {
            app.showMessage(
                $.t('ovs:vdisks.remove_snapshot.title_msg', {what: snapshotid}),
                $.t('ovs:generic.are_you_sure'),
                [$.t('ovs:generic.no'), $.t('ovs:generic.yes')]
            )
            .done(function(answer) {
                if (answer === $.t('ovs:generic.yes')) {
                    generic.alertInfo(
                        $.t('ovs:vdisks.remove_snapshot.started'),
                        $.t('ovs:vdisks.remove_snapshot.started_msg', {what: snapshotid})
                    );
                    vDiskService.removeSnapshot(self.vDisk.guid(), snapshotid)
                        .done(function () {
                            generic.alertSuccess(
                                $.t('ovs:vdisks.remove_snapshot.success'),
                                $.t('ovs:vdisks.remove_snapshot.success_msg', {what: snapshotid})
                            );
                        })
                        .fail(function (error) {
                            error = generic.extractErrorMessage(error);
                            generic.alertError(
                                $.t('ovs:generic.error'),
                                $.t('ovs:vdisks.remove_snapshot.failed_msg', {what: snapshotid, why: error})
                            );
                        })
                        .always(function () {
                            self.load();
                        });
            }});
        };
        self.removeVDisk = function() {
            if (self.vDisk === undefined || self.vDisk.childrenGuids().length !== 0) {
                return false
            }
            var vd = self.vDisk;
            self.removing(true);
            app.showMessage(
                    $.t('ovs:vdisks.remove_vdisk.title_msg', {what: vd.name()}),
                    $.t('ovs:generic.are_you_sure'),
                    [$.t('ovs:generic.no'), $.t('ovs:generic.yes')]
                )
                .done(function(answer) {
                    if (answer !== $.t('ovs:generic.yes')) {
                        self.removing(false)
                    }
                    generic.alertInfo(
                        $.t('ovs:vdisks.remove_vdisk.started'),
                        $.t('ovs:vdisks.remove_vdisk.started_msg', {what: vd.name()})
                    );
                    vDiskService.removeVDisk(vd.guid())
                        .done(function() {
                            generic.alertSuccess(
                                $.t('ovs:vdisks.remove_vdisk.success'),
                                $.t('ovs:vdisks.remove_vdisk.success_msg', {what: vd.name()})
                            );
                            router.navigateBack();
                        })
                        .fail(function(error) {
                            error = generic.extractErrorMessage(error);
                            generic.alertError(
                                $.t('ovs:generic.error'),
                                $.t('ovs:vdisks.remove_vdisk.failed_msg', {what: vd.name(), why: error})
                            );
                        })
                        .always(function() {
                            self.removing(false);
                        });
                });

        };
        self.restartVDisk = function() {
            if (self.vDisk === undefined || self.vDisk.liveStatus() === 'RUNNING') {
                return false;
            }
            var vd = self.vDisk;
            self.restarting(true);
            app.showMessage(
                    $.t('ovs:vdisks.restart_vdisk.title_msg', {what: vd.name()}),
                    $.t('ovs:generic.are_you_sure'),
                    [$.t('ovs:generic.no'), $.t('ovs:generic.yes')]
                )
                .done(function(answer) {
                    if (answer !== $.t('ovs:generic.yes')) {
                        self.restarting(false);
                    }
                    generic.alertInfo(
                        $.t('ovs:vdisks.restart_vdisk.started'),
                        $.t('ovs:vdisks.restart_vdisk.started_msg', {what: vd.name()})
                    );
                    vDiskService.restart(vd.guid())
                        .done(function() {
                            generic.alertSuccess(
                                $.t('ovs:vdisks.restart_vdisk.success'),
                                $.t('ovs:vdisks.restart_vdisk.success_msg', {what: vd.name()})
                            );
                        })
                        .fail(function(error) {
                            error = generic.extractErrorMessage(error);
                            generic.alertError(
                                $.t('ovs:generic.error'),
                                $.t('ovs:vdisks.restart_vdisk.failed_msg', {what: vd.name(), why: error})
                            );
                        })
                        .always(function() {
                            self.restarting(false);
                        });

            });

        };

        // Computed
        self.canBeModified = ko.pureComputed(function() {
            if (self.vDisk === undefined) {
                return false;
            }
            return !self.convertingToTemplate() && !self.removing() && !self.restarting() && self.vDisk.liveStatus() === 'RUNNING';
        });
        self.canBeTemplated = ko.pureComputed(function() {
            if (self.vDisk === undefined) {
                return false;
            }
            return self.vDisk.parentVDiskGuid() === null && self.vDisk.childrenGuids().length === 0;
        });
        self.tooltipSetAsTemplate = ko.pureComputed(function(){
            if (self.vDisk === undefined) {
                return '';
            }
            if (self.vDisk.childrenGuids().length > 0) {
                return $.t('ovs:vdisks.detail.has_children');
            }
            if (self.vDisk.parentVDiskGuid() !== null) {
                return $.t('ovs:vdisks.detail.is_clone');
            }
            return $.t('ovs:vdisks.detail.set_as_template');
        });
        self.equalsDefaultCacheQuota = ko.pureComputed(function() {
            var allFalse = {fragment: false, block: false};
            if (self.vDisk === undefined || self.vDisk.configuration() === undefined) {
                return allFalse;
            }
            var vPool = self.vDisk.vpool();
            if (vPool === undefined || vPool.metadata() === undefined) {
                return allFalse;
            }
            if (vPool.metadata().caching_info.hasOwnProperty(self.vDisk.storageRouterGuid())) {
                var cachingInfo = vPool.metadata().caching_info[self.vDisk.storageRouterGuid()];
                var vPoolFragment = generic.tryGet(cachingInfo.fragment_cache, 'quota', null);
                var vPoolBlock = generic.tryGet(cachingInfo.block_cache, 'quota', null);
                var vDiskFragment = self.vDisk.fragmentCQ() !== undefined && self.vDisk.fragmentCQ() !== '' ? Math.round(self.vDisk.fragmentCQ() * Math.pow(1024.0, 3)) : null;
                var vDiskBlock = self.vDisk.blockCQ() !== undefined && self.vDisk.blockCQ() !== '' ? Math.round(self.vDisk.blockCQ() * Math.pow(1024.0, 3)) : null;
                return {fragment: vPoolFragment === vDiskFragment, block: vPoolBlock === vDiskBlock};
            }
            return allFalse;
        });
        self.hasCacheQuota = ko.pureComputed(function() {
            if (self.vDisk === undefined || self.vDisk.storageRouter() === undefined || self.vDisk.storageRouter().features() === undefined) {
                return false;
            } else {
                var features = self.vDisk.storageRouter().features();
                return features.alba.features !== undefined && features.alba.features.contains('cache-quota');
            }
        });
        self.hasBlockCache = ko.pureComputed(function() {
            if (self.vDisk === undefined || self.vDisk.storageRouter() === undefined || self.vDisk.storageRouter().features() === undefined) {
                return false;
            } else {
                var features = self.vDisk.storageRouter().features();
                return features.alba.features !== undefined && features.alba.features.contains('block-cache');
            }
        });
        self.canManage = ko.pureComputed(function() {
            return authentication.user.canManage()
        });
        self.canWriteAndModify = ko.pureComputed(function() {
             return authentication.user.canWrite() && self.canBeModified()
        });
        self.canManageAndModify = ko.pureComputed(function() {
            return self.canManage() && self.canBeModified()
        });
        self.canSnapshot = ko.pureComputed(function() {
            return self.canWriteAndModify() && !self.data.blocked_actions().contains('snapshot');
        });
        self.canRollBack = ko.pureComputed(function() {
            return self.canWriteAndModify() && !self.data.blocked_actions().contains('rollback');
        });
        self.canClone = ko.pureComputed(function() {
            return self.canWriteAndModify() && !self.data.blocked_actions().contains('clone');
        });
        self.canMove = ko.pureComputed(function() {
            return self.canWriteAndModify() && !self.data.blocked_actions().contains('move');
        });
        self.canScrub = ko.pureComputed(function() {
            return self.canManageAndModify() && !self.data.blocked_actions().contains('scrub');
        });
        self.canSetAsTemplate = ko.pureComputed(function() {
            return self.canWriteAndModify() && !self.data.blocked_actions().contains('set_as_template');
        });
        self.canRestart = ko.pureComputed(function() {
            return self.canManageAndModify() && !self.data.blocked_actions().contains('restart') && self.vDisk.liveStatus() !== 'RUNNING';
        });
        self.canRemove = ko.pureComputed(function() {
            return self.canManageAndModify() && !self.data.blocked_actions().contains('remove') && !self.removing() && !self.convertingToTemplate();
        });
        // Durandal
        self.activate = function (mode, guid) {
                    self.plugin_pages = pluginLoader.get_plugin_pages(self.vDiskString, guid);
                    $.each(self.plugin_pages, function(index, page) {
                        pluginLoader.activate_page(page)
                    });

                    // Creating a new data will evit an event which will trigger in the loaded plugins
                    self.data = new vdiskDetailData.constructor();
                    self.vDisk = self.data.vdisk;  // The view binding happens after the activate. Reference the same vdisk object as in the shared data
                    self.vDisk.guid(guid);  // Set the guid of the vdisk which is fetched from the router;
                    self.refresher.init(self.load, 5000);
                    self.refresher.run();
                    self.refresher.start();

        };
        self.deactivate = function() {
            var pages = pluginLoader.get_plugin_pages(self.vDiskString, self.vDisk.guid())
            $.each(pages, function (pageType, page) {
                pluginLoader.deactivate_page(page)
            });

            $.each(self.widgets, function(index, item) {
                item.deactivate();
            });
            self.refresher.stop();
        };
    };
});
