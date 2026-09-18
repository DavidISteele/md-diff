/* The document's own headings, for the outline pane.
 *
 * The counterpart of nav.js and find.js: the host evaluates this script
 * with a call substituted for __CALL__ and reads the string it returns.
 * collect() hands back the headings as one string -- "level<TAB>text", one
 * per line, in document order -- because a string is what comes back across
 * evaluate_javascript, and a line of two fields needs no JSON parser on the
 * C side to take apart.  go(i) scrolls to the i-th of the same list, so the
 * pane never has to name a heading it wants: the index it was built from is
 * enough, and headings the renderer gave no id are reachable too.
 */
(function () {
  function textOf(node) {
    return (node.textContent || '').replace(/\s+/g, ' ').trim();
  }

  if (!window.__mdDiffToc) {
    window.__mdDiffToc = {
      nodes: [],

      /* Sections only.  Pandoc's title block is the document's name rather
         than a place inside it, and its own --toc is a list of links that
         happens to sit under a heading -- neither is somewhere to scroll
         to.  An empty heading is dropped so the pane has no blank rows,
         which also keeps the lines and `nodes` in step. */
      collect: function () {
        this.nodes = Array.prototype.slice
          .call(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
          .filter(function (node) {
            if (node.closest('#title-block-header') || node.closest('#TOC'))
              return false;
            return textOf(node) !== '';
          });
        return this.nodes.map(function (node) {
          return node.tagName.charAt(1) + '\t' + textOf(node);
        }).join('\n');
      },

      go: function (index) {
        /* The pane can only have been filled by a collect(), but a reload
           would leave this script's object behind with its nodes gone. */
        if (!this.nodes.length) this.collect();
        var node = this.nodes[index];
        if (!node) return '';
        node.scrollIntoView({ block: 'start', behavior: 'smooth' });
        return '';
      }
    };
  }
  return window.__mdDiffToc.__CALL__;
})()
