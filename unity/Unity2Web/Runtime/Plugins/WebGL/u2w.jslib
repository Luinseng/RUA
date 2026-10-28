// Browser side of Unity2Web: persists Application.persistentDataPath (Emscripten IDBFS) to IndexedDB.
var Unity2WebLibrary = {
  $U2WState: { syncing: false, pending: false, warned: false },

  U2W_SyncFS__deps: ['$U2WState', '$FS'],
  U2W_SyncFS: function () {
    if (typeof FS === 'undefined' || !FS.syncfs) {
      if (!U2WState.warned) { U2WState.warned = true; console.error('[u2w] FS.syncfs unavailable: saves will not persist'); }
      return;
    }
    if (U2WState.syncing) { U2WState.pending = true; return; }
    U2WState.syncing = true;
    FS.syncfs(false, function (err) {
      U2WState.syncing = false;
      if (err) console.warn('[u2w] IndexedDB sync failed', err);
      if (U2WState.pending) { U2WState.pending = false; _U2W_SyncFS(); }
    });
  },

  U2W_InstallPageHooks__deps: ['U2W_SyncFS'],
  U2W_InstallPageHooks: function () {
    var flush = function () { _U2W_SyncFS(); };
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') flush();
    });
  }
};

mergeInto(LibraryManager.library, Unity2WebLibrary);
