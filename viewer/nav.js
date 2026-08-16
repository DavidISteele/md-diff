(function () {
  var SEL = 'ins, del, .diff-added-section, .diff-removed-section,' +
            '.diff-added-block, .diff-removed-block,' +
            'tr.diff-row-inserted, tr.diff-row-deleted, tr.diff-row-changed';

  var KINDS = [
    ['ins, .diff-added-section, .diff-added-block, tr.diff-row-inserted', 'add'],
    ['del, .diff-removed-section, .diff-removed-block, tr.diff-row-deleted', 'del']
  ];

  function kindOf(node) {
    for (var i = 0; i < KINDS.length; i++) {
      if (node.matches(KINDS[i][0])) return KINDS[i][1];
    }
    return 'chg';
  }

  if (!window.__mdDiffNav) {
    window.__mdDiffNav = {
      idx: -1,
      nodes: [],
      marks: [],
      refresh: function () {
        var all = Array.prototype.slice.call(document.querySelectorAll(SEL))
          .filter(function (n) { return !n.closest('.diff-legend'); });
        this.nodes = all.filter(function (n) {
          return !all.some(function (o) { return o !== n && o.contains(n); });
        });
        if (this.idx >= this.nodes.length) this.idx = this.nodes.length - 1;
        return this.nodes.length;
      },
      label: function () {
        if (!this.nodes.length) return 'no changes';
        if (this.idx < 0) return this.nodes.length + ' changes';
        return (this.idx + 1) + ' / ' + this.nodes.length;
      },
      go: function (delta) {
        if (!this.nodes.length) return this.label();
        this.idx = (this.idx + delta + this.nodes.length) % this.nodes.length;
        var n = this.nodes[this.idx];
        Array.prototype.forEach.call(
          document.querySelectorAll('.md-diff-current'),
          function (e) { e.classList.remove('md-diff-current'); });
        n.classList.add('md-diff-current');
        n.scrollIntoView({ block: 'center', behavior: 'smooth' });
        this.markCurrent();
        return this.label();
      },

      // --- Scrollbar map ---

      buildMap: function () {
        var self = this;
        this.mapEl = document.createElement('div');
        this.mapEl.className = 'md-diff-map';
        this.thumbEl = document.createElement('div');
        this.thumbEl.className = 'md-diff-map-thumb';
        this.mapEl.appendChild(this.thumbEl);
        document.body.appendChild(this.mapEl);

        this.mapEl.addEventListener('pointerdown', function (e) {
          self.mapEl.setPointerCapture(e.pointerId);
          self.mapEl.classList.add('dragging');
          // Grabbing the thumb keeps the grab point under the cursor;
          // clicking the track jumps so the thumb centres there.
          var box = self.thumbEl.getBoundingClientRect();
          self.grab = (e.target === self.thumbEl) ? e.clientY - box.top : null;
          self.dragTo(e.clientY);
          e.preventDefault();
        });
        this.mapEl.addEventListener('pointermove', function (e) {
          if (self.mapEl.classList.contains('dragging')) self.dragTo(e.clientY);
        });
        ['pointerup', 'pointercancel'].forEach(function (name) {
          self.mapEl.addEventListener(name, function () {
            self.mapEl.classList.remove('dragging');
          });
        });

        window.addEventListener('scroll', function () {
          self.schedule(false);
        }, { passive: true });
        window.addEventListener('resize', function () { self.schedule(true); });
        // Late-loading images and font swaps reflow the document, which moves
        // every marker.
        if (window.ResizeObserver) {
          new ResizeObserver(function () { self.schedule(true); })
            .observe(document.body);
        }
        this.layout();
      },

      // Coalesce bursts of scroll/resize events into one frame.
      schedule: function (relayout) {
        var self = this;
        this.relayout = this.relayout || relayout;
        if (this.pending) return;
        this.pending = requestAnimationFrame(function () {
          self.pending = 0;
          if (self.relayout) { self.relayout = false; self.layout(); }
          else self.syncThumb();
        });
      },

      layout: function () {
        var doc = document.documentElement;
        // Nothing to overview when the whole document already fits.
        if (doc.scrollHeight - doc.clientHeight <= 1) {
          this.mapEl.style.display = 'none';
          return;
        }
        this.mapEl.style.display = '';

        this.marks.forEach(function (m) { m.remove(); });
        this.marks = [];

        var track = this.mapEl.clientHeight;
        var self = this;
        this.nodes.forEach(function (n) {
          var rect = n.getBoundingClientRect();
          var height = Math.max(3, rect.height / doc.scrollHeight * track);
          var top = (rect.top + window.scrollY) / doc.scrollHeight * track;
          var mark = document.createElement('div');
          mark.className = 'md-diff-map-mark ' + kindOf(n);
          mark.style.top = Math.max(0, Math.min(track - height, top)) + 'px';
          mark.style.height = height + 'px';
          self.mapEl.insertBefore(mark, self.thumbEl);  // thumb stays on top
          self.marks.push(mark);
        });

        this.markCurrent();
        this.syncThumb();
      },

      markCurrent: function () {
        this.marks.forEach(function (m, i) {
          m.classList.toggle('current', i === this.idx);
        }, this);
      },

      // Thumb spans the visible fraction, positioned on the same document
      // scale as the markers, so it frames the changes it is level with.
      syncThumb: function () {
        var doc = document.documentElement;
        var track = this.mapEl.clientHeight;
        var height = Math.max(20, track * doc.clientHeight / doc.scrollHeight);
        var top = window.scrollY / doc.scrollHeight * track;
        this.thumbEl.style.height = height + 'px';
        this.thumbEl.style.top =
          Math.max(0, Math.min(track - height, top)) + 'px';
      },

      dragTo: function (clientY) {
        var doc = document.documentElement;
        var track = this.mapEl.clientHeight;
        var height = this.thumbEl.offsetHeight;
        var grab = (this.grab === null) ? height / 2 : this.grab;
        var top = clientY - this.mapEl.getBoundingClientRect().top - grab;
        var max = doc.scrollHeight - doc.clientHeight;
        window.scrollTo(0, Math.max(0, Math.min(
          max, top / track * doc.scrollHeight)));
      }
    };
    window.__mdDiffNav.refresh();
    window.__mdDiffNav.buildMap();
  }
  return window.__mdDiffNav.__CALL__;
})()
