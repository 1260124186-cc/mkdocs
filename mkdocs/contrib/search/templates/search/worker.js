var base_path = 'function' === typeof importScripts ? '.' : '/search/';
var allowSearch = false;
var index;
var documents = {};
var lang = ['en'];
var data;

// Shard mode state. When a shard manifest is published by the build, the
// worker loads chapter indexes lazily instead of downloading the full index.
var shardMode = false;
var manifest = null;
var shardIndexes = {};
var shardDocuments = {};
var activeShards = [];
var loadedShards = {};
var loadingShards = {};
var currentShard = null;
var pendingQuery = null;

function getScript(script, callback) {
  console.log('Loading script: ' + script);
  $.getScript(base_path + script).done(function () {
    callback();
  }).fail(function (jqxhr, settings, exception) {
    console.log('Error: ' + exception);
  });
}

function getScriptsInOrder(scripts, callback) {
  if (scripts.length === 0) {
    callback();
    return;
  }
  getScript(scripts[0], function() {
    getScriptsInOrder(scripts.slice(1), callback);
  });
}

function loadScripts(urls, callback) {
  if( 'function' === typeof importScripts ) {
    importScripts.apply(null, urls);
    callback();
  } else {
    getScriptsInOrder(urls, callback);
  }
}

// Resolve a path relative to the search directory in both worker and
// main-thread (fallback) contexts.
function searchUrl(path) {
  if( 'function' === typeof importScripts ){
    return path;
  }
  return base_path + path;
}

function fetchJSON(path, onLoad, onError) {
  var oReq = new XMLHttpRequest();
  oReq.addEventListener('load', function () {
    if (this.status >= 200 && this.status < 400) {
      onLoad(JSON.parse(this.responseText));
    } else if (onError) {
      onError();
    }
  });
  oReq.addEventListener('error', function () {
    if (onError) {
      onError();
    }
  });
  oReq.open("GET", path);
  oReq.send();
}

function scriptsForLang(l) {
  var scriptsToLoad = ['lunr.js'];
  if (l.length > 1 || l[0] !== "en") {
    scriptsToLoad.push('lunr.stemmer.support.js');
    if (l.length > 1) {
      scriptsToLoad.push('lunr.multi.js');
    }
    if (l.includes("ja") || l.includes("jp")) {
      scriptsToLoad.push('tinyseg.js');
    }
    for (var i=0; i < l.length; i++) {
      if (l[i] != 'en') {
        scriptsToLoad.push(['lunr', l[i], 'js'].join('.'));
      }
    }
  }
  return scriptsToLoad;
}

function applySeparator(config) {
  if (config && config.separator && config.separator.length) {
    lunr.tokenizer.separator = new RegExp(config.separator);
  }
}

function buildLunrIndex(docs, l) {
  return lunr(function () {
    if (l.length === 1 && l[0] !== "en" && lunr[l[0]]) {
      this.use(lunr[l[0]]);
    } else if (l.length > 1) {
      this.use(lunr.multiLanguage.apply(null, l));  // spread operator not supported in all browsers: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Spread_operator#Browser_compatibility
    }
    this.field('title');
    this.field('text');
    this.ref('location');

    for (var i=0; i < docs.length; i++) {
      var doc = docs[i];
      this.add(doc);
    }
  });
}

function onJSONLoaded () {
  data = JSON.parse(this.responseText);
  if (data.config && data.config.lang && data.config.lang.length) {
    lang = data.config.lang;
  }
  loadScripts(scriptsForLang(lang), onScriptsLoaded);
}

function onScriptsLoaded () {
  console.log('All search scripts loaded, building Lunr index...');
  applySeparator(data.config);

  if (data.index) {
    index = lunr.Index.load(data.index);
    data.docs.forEach(function (doc) {
      documents[doc.location] = doc;
    });
    console.log('Lunr pre-built index loaded, search ready');
  } else {
    index = buildLunrIndex(data.docs, lang);
    data.docs.forEach(function (doc) {
      documents[doc.location] = doc;
    });
    console.log('Lunr index built, search ready');
  }
  allowSearch = true;
  postMessage({config: data.config});
  postMessage({allowSearch: allowSearch});
}

function normalizePagePath(path) {
  if (!path) {
    return '';
  }
  return decodeURIComponent(path).replace(/^\/+|\/+$/g, '');
}

function shardForPage(pagePath) {
  var page = normalizePagePath(pagePath);
  var best = null;
  var bestLength = -1;
  manifest.shards.forEach(function (shard) {
    for (var i=0; i < shard.locations.length; i++) {
      var loc = normalizePagePath(shard.locations[i]);
      if (page === loc || (loc !== '' && page.indexOf(loc + '/') === 0)) {
        if (loc.length > bestLength) {
          best = shard.slug;
          bestLength = loc.length;
        }
      }
    }
  });
  return best;
}

function shardMeta(slug) {
  for (var i=0; i < manifest.shards.length; i++) {
    if (manifest.shards[i].slug === slug) {
      return manifest.shards[i];
    }
  }
  return null;
}

function onManifestLoaded(manifestData, pagePath) {
  console.log('Search shard manifest loaded, indexes will be loaded per chapter.');
  manifest = manifestData;
  shardMode = true;
  if (manifest.config && manifest.config.lang && manifest.config.lang.length) {
    lang = manifest.config.lang;
  }
  loadScripts(scriptsForLang(lang), function () {
    onShardScriptsLoaded(pagePath);
  });
}

function onShardScriptsLoaded(pagePath) {
  applySeparator(manifest.config);
  allowSearch = true;
  currentShard = shardForPage(pagePath);
  postMessage({config: manifest.config});
  postMessage({
    shards: {
      current: currentShard,
      list: manifest.shards
    }
  });
  postMessage({allowSearch: allowSearch});
  if (currentShard) {
    setActiveShards([currentShard]);
  } else {
    // The current page could not be mapped to a chapter; search everything.
    setAllShardsActive();
  }
}

function loadShard(slug, onShardReady) {
  if (loadedShards[slug]) {
    onShardReady();
    return;
  }
  if (loadingShards[slug]) {
    loadingShards[slug].push(onShardReady);
    return;
  }
  loadingShards[slug] = [onShardReady];
  var meta = shardMeta(slug);
  if (!meta) {
    finishLoadingShard(slug);
    return;
  }
  fetchJSON(searchUrl(meta.location), function (shardData) {
    var docs = {};
    var shardIndex;
    applySeparator(shardData.config);
    if (shardData.index) {
      shardIndex = lunr.Index.load(shardData.index);
    } else {
      shardIndex = buildLunrIndex(shardData.docs, lang);
    }
    shardData.docs.forEach(function (doc) {
      docs[doc.location] = doc;
    });
    shardIndexes[slug] = shardIndex;
    shardDocuments[slug] = docs;
    finishLoadingShard(slug);
  }, function () {
    console.error('Could not load search shard: ' + slug);
    finishLoadingShard(slug);
  });
}

function finishLoadingShard(slug) {
  loadedShards[slug] = true;
  var callbacks = loadingShards[slug] || [];
  delete loadingShards[slug];
  for (var i=0; i < callbacks.length; i++) {
    callbacks[i]();
  }
}

function activeShardsLoaded() {
  for (var i=0; i < activeShards.length; i++) {
    if (!loadedShards[activeShards[i]]) {
      return false;
    }
  }
  return true;
}

// Once every active shard is available, answer any query that arrived while
// the chapter indexes were still being downloaded.
function onActiveShardsLoaded() {
  postMessage({shardReady: true});
  if (pendingQuery !== null) {
    var query = pendingQuery;
    pendingQuery = null;
    postMessage({results: search(query)});
  }
}

function ensureActiveShardsLoaded() {
  var pending = [];
  activeShards.forEach(function (slug) {
    if (!loadedShards[slug]) {
      pending.push(slug);
    }
  });
  if (pending.length === 0) {
    onActiveShardsLoaded();
    return;
  }
  var remaining = pending.length;
  pending.forEach(function (slug) {
    loadShard(slug, function () {
      remaining -= 1;
      if (remaining === 0) {
        onActiveShardsLoaded();
      }
    });
  });
}

function setActiveShards(slugs) {
  activeShards = slugs;
  ensureActiveShardsLoaded();
}

function setAllShardsActive() {
  var slugs = [];
  manifest.shards.forEach(function (shard) {
    slugs.push(shard.slug);
  });
  setActiveShards(slugs);
}

function loadFullIndex () {
  var oReq = new XMLHttpRequest();
  oReq.addEventListener("load", onJSONLoaded);
  oReq.open("GET", searchUrl('search_index.json'));
  oReq.send();
}

function init (msg) {
  if ('function' !== typeof importScripts) {
    // Main-thread fallback: no worker is available to drive lazy shard
    // loading, so keep using the always-published full index.
    loadFullIndex();
    return;
  }
  // Prefer the chapter shard manifest. When it is not published (older build
  // output or sharding disabled), fall back to the full search index.
  fetchJSON(searchUrl('shards/manifest.json'), function (manifestData) {
    onManifestLoaded(manifestData, msg && msg.page);
  }, loadFullIndex);
}

function search (query) {
  if (!allowSearch) {
    console.error('Assets for search still loading');
    return;
  }

  var resultDocuments = [];
  if (shardMode) {
    var collected = [];
    activeShards.forEach(function (slug) {
      if (!loadedShards[slug] || !shardIndexes[slug]) {
        return;
      }
      var results = shardIndexes[slug].search(query);
      for (var i=0; i < results.length; i++){
        var result = results[i];
        var doc = shardDocuments[slug][result.ref];
        if (doc) {
          doc.summary = doc.text.substring(0, 200);
          collected.push({doc: doc, score: result.score});
        }
      }
    });
    collected.sort(function (a, b) {
      return b.score - a.score;
    });
    for (var j=0; j < collected.length; j++) {
      resultDocuments.push(collected[j].doc);
    }
  } else {
    var legacyResults = index.search(query);
    for (var k=0; k < legacyResults.length; k++){
      var legacyResult = legacyResults[k];
      var legacyDoc = documents[legacyResult.ref];
      legacyDoc.summary = legacyDoc.text.substring(0, 200);
      resultDocuments.push(legacyDoc);
    }
  }
  return resultDocuments;
}

if( 'function' === typeof importScripts ) {
  onmessage = function (e) {
    if (e.data.init) {
      init(e.data);
    } else if (e.data.query) {
      if (shardMode && !activeShardsLoaded()) {
        pendingQuery = e.data.query;
      } else {
        postMessage({ results: search(e.data.query) });
      }
    } else if (shardMode && e.data.shard) {
      setActiveShards([e.data.shard]);
    } else if (shardMode && e.data.allShards) {
      setAllShardsActive();
    } else {
      console.error("Worker - Unrecognized message: " + e);
    }
  };
}
