/* Keyword search over the rendered document.
 *
 * The counterpart of nav.js: the host evaluates this script with a call
 * substituted for __CALL__ and shows the string it returns beside the
 * search box.  WebKitFindController would do the highlighting for free but
 * only ever reports how many matches exist, never which one you are on, and
 * "3 / 17" is the whole point of the counter.
 *
 * Matching is per text node, so a phrase that straddles an element boundary
 * -- "the word" where "word" is an <ins> in a diff -- is not found.
 * Flattening the document to one string and splitting nodes around a hit
 * would fix that at the cost of rewriting the very markup the diff exists to
 * show; a keyword search does not need it.
 */
(function () {
  var STYLE =
    '.md-find-hit { background: #fff3a3; color: #1f2328; padding: 0;' +
    ' border-radius: 2px; }' +
    '.md-find-hit.md-find-current { background: #ff8c1a;' +
    ' outline: 2px solid #ff8c1a; scroll-margin: 40vh; }' +
    '@media (prefers-color-scheme: dark) {' +
    ' .md-find-hit { background: #7a5c00; color: #f0f6fc; }' +
    ' .md-find-hit.md-find-current { background: #b35900;' +
    ' outline-color: #b35900; } }';

  function addStyle() {
    if (document.getElementById('md-find-style')) return;
    var el = document.createElement('style');
    el.id = 'md-find-style';
    el.textContent = STYLE;
    (document.head || document.documentElement).appendChild(el);
  }

  /* Every text node worth searching: no script or style content, and not the
     scrollbar map, which nav.js builds out of empty divs anyway. */
  function textNodes() {
    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          var parent = node.parentNode;
          if (!node.nodeValue || parent == null) return NodeFilter.FILTER_REJECT;
          var tag = parent.nodeName;
          if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT')
            return NodeFilter.FILTER_REJECT;
          if (parent.closest && parent.closest('.md-diff-map'))
            return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });
    var nodes = [], node;
    while ((node = walker.nextNode())) nodes.push(node);
    return nodes;
  }

  /* Replace one text node with the same text, each match wrapped in a mark.
     Offsets come from the lowercased copy, which is only safe while casing
     preserves length -- Turkish dotted I and the German sharp s do not, so
     those fall back to a case-sensitive pass rather than slicing blind. */
  function highlight(node, needle, raw, hits) {
    var text = node.nodeValue;
    var folded = text.toLowerCase();
    if (folded.length !== text.length) { folded = text; needle = raw; }

    var at = folded.indexOf(needle);
    if (at < 0) return;

    var fragment = document.createDocumentFragment();
    var pos = 0;
    while (at >= 0) {
      if (at > pos)
        fragment.appendChild(document.createTextNode(text.slice(pos, at)));
      var mark = document.createElement('mark');
      mark.className = 'md-find-hit';
      mark.textContent = text.slice(at, at + needle.length);
      fragment.appendChild(mark);
      hits.push(mark);
      pos = at + needle.length;
      at = folded.indexOf(needle, pos);
    }
    if (pos < text.length)
      fragment.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(fragment, node);
  }

  if (!window.__mdDiffFind) {
    window.__mdDiffFind = {
      idx: -1,
      hits: [],
      query: '',

      /* Put the document back as it was: unwrap every mark, then merge the
         text nodes the unwrapping left adjacent, so a second search sees
         whole strings again and matches that span a former hit. */
      clear: function () {
        var parents = [];
        this.hits.forEach(function (mark) {
          var parent = mark.parentNode;
          if (parent == null) return;
          parent.replaceChild(document.createTextNode(mark.textContent), mark);
          if (parents.indexOf(parent) < 0) parents.push(parent);
        });
        parents.forEach(function (parent) { parent.normalize(); });
        this.hits = [];
        this.idx = -1;
        this.query = '';
      },

      search: function (query) {
        this.clear();
        if (!query) return this.label();
        addStyle();
        this.query = query;
        var hits = this.hits;
        var needle = query.toLowerCase();
        textNodes().forEach(function (node) {
          highlight(node, needle, query, hits);
        });
        if (hits.length) this.go(1);
        return this.label();
      },

      go: function (delta) {
        if (!this.hits.length) return this.label();
        this.idx = (this.idx + delta + this.hits.length) % this.hits.length;
        this.hits.forEach(function (mark, i) {
          mark.classList.toggle('md-find-current', i === this.idx);
        }, this);
        this.hits[this.idx].scrollIntoView(
          { block: 'center', behavior: 'smooth' });
        return this.label();
      },

      label: function () {
        if (!this.query) return '';
        if (!this.hits.length) return 'no matches';
        return (this.idx + 1) + ' / ' + this.hits.length;
      }
    };
  }
  return window.__mdDiffFind.__CALL__;
})()
