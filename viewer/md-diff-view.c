/*
 * md-diff-view — show one rendered HTML document in a window.
 *
 * A compiled binary rather than a Python script because AppArmor profiles
 * attach to the executable the kernel loads.  Ubuntu 24.04 grants
 * unprivileged user namespaces per-executable, and WebKit's sandbox cannot
 * start without them; profiling /usr/bin/python3 would grant them to every
 * Python program on the machine, which is no boundary at all.  This binary
 * only ever renders a document handed to it, so a profile naming it is a
 * grant worth making.  See packaging/apparmor/md-diff-view.
 *
 * The document arrives already rendered, on stdin or as a file argument.
 * Everything markdown-shaped -- pandoc, the diff, the stylesheet -- stays in
 * md_diff; this program contributes the window, the change stepper, the
 * keyword search and the scrollbar map.
 *
 * Usage: md-rich-diff old.md new.md --stdout | md-diff-view --title "..."
 */

#include <errno.h>
#include <stdio.h>

#include <gtk/gtk.h>
#include <webkit/webkit.h>

#include "nav_js.h"    /* NAV_JS, generated from nav.js by the Makefile */
#include "find_js.h"   /* FIND_JS, likewise from find.js */

#define SANDBOX_ENV "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"

/* Shared by the GTK application, the desktop entry's filename and the icon
   installed into the hicolor theme.  The three matching is what lets a
   desktop pair the window with its launcher. */
#define APP_ID "org.user.local.md-diff-view"

static const char SANDBOX_HELP[] =
"Error: WebKit cannot start its sandbox on this system.\n"
"Reason: unprivileged user namespaces are unavailable to this program.\n"
"\n"
"WebKit renders page content in a bubblewrap sandbox, which needs\n"
"unprivileged user namespaces.  Ubuntu 24.04 and later restrict these to\n"
"programs an AppArmor profile grants them to, so the web process dies with\n"
"\"bwrap: setting up uid map: Permission denied\".\n"
"\n"
"Two ways forward:\n"
"\n"
"  1. Install the profile that grants this binary -- and only this binary --\n"
"     the permission, keeping the sandbox:\n"
"\n"
"         sudo cp packaging/apparmor/md-diff-view /etc/apparmor.d/\n"
"         sudo apparmor_parser -r /etc/apparmor.d/md-diff-view\n"
"\n"
"  2. Run without the sandbox (no root, affects nothing else):\n"
"\n"
"         md-diff-view ... --no-sandbox\n"
"\n"
"Option 1 is the better trade.  Under option 2 a document that reaches a\n"
"bug in the renderer is running as your user account rather than inside an\n"
"empty cell -- and this tool exists to render markdown out of branches\n"
"other people wrote.\n";

typedef struct Window Window;

/* One document, in one tab.  Heap-allocated and owned by its notebook page,
   because in session mode a single process serves many invocations and each
   arrives with its own document and its own captions -- the option globals
   describe whichever invocation is being parsed, never what is on screen. */
typedef struct {
    Window *win;             /* the window this document is showing in */
    WebKitWebView *webview;
    GString *document;
    char *title;
    char *heading;
    char *subheading;
    gboolean navigation;     /* the stepper suits a diff, not a single file */
    gboolean loaded;         /* first load done: later navigation is denied */
} Viewer;

/* The chrome, which belongs to the window rather than to any one document:
   the header caption, the stepper and the search bar all retarget to
   whichever tab is in front.  Keeping one of each -- rather than a set per
   tab -- is what lets a search follow you from document to document. */
struct Window {
    GtkWidget *window;
    GtkWidget *notebook;
    GtkWidget *header;
    GtkWidget *stepper;      /* the two step buttons, hidden for a document */
    GtkWidget *counter;
    GtkWidget *search_bar;
    GtkWidget *search_entry;
    GtkWidget *search_counter;
    GtkWidget *tabs_button;  /* NULL outside a session */
    gboolean closing;        /* window teardown, not a tab being closed */
};

/* The tab strip earns its space only when there is a choice to make, so one
   document hides it -- but a hidden strip is also nothing to drag, and
   nothing to drop onto, which leaves two single-document windows unable to
   be recombined.  Hence the pin: it belongs to the session rather than to a
   window, because docking needs a strip at both ends and asking for it twice
   would be a poor way to spend a click. */
static gboolean tabs_pinned = FALSE;
static gboolean tabs_syncing = FALSE;

static Viewer *current_viewer(Window *win);
static void close_tab(Window *win);
static void step_tab(Window *win, int delta);
static void detach_tab(Window *win);
static void toggle_tabs(Window *win);
static Window *window_new(GtkApplication *app);
static GtkWidget *build_tab_label(GtkWidget *page, Viewer *viewer);

static char *opt_title = NULL;
static char *opt_heading = NULL;
static char *opt_subheading = NULL;
static gboolean opt_no_navigation = FALSE;
static gboolean opt_no_sandbox = FALSE;
static gboolean opt_check_sandbox = FALSE;
static char *opt_session = NULL;
/* Settled once, in main.  opt_session is re-parsed for every invocation that
   joins, so it says what the last one asked for rather than what this
   process is. */
static gboolean session_mode = FALSE;
static char **opt_files = NULL;

/* The text options are G_OPTION_ARG_FILENAME, which GLib hands back as the
   bytes it was given.  G_OPTION_ARG_STRING would convert them from the
   locale charset -- and the charset is resolved before gtk_init() gets to
   call setlocale(), so it is ASCII whatever the environment says, and the
   arrow in an "old → new" title fails with "invalid byte sequence".  The
   captions are UTF-8 by construction and UTF-8 is what GTK wants. */
static const GOptionEntry OPTIONS[] = {
    { "title", 't', 0, G_OPTION_ARG_FILENAME, &opt_title,
      "Window title", "TEXT" },
    { "heading", 0, 0, G_OPTION_ARG_FILENAME, &opt_heading,
      "Header bar title (default: the window title)", "TEXT" },
    { "subheading", 0, 0, G_OPTION_ARG_FILENAME, &opt_subheading,
      "Second header bar line", "TEXT" },
    { "no-navigation", 0, 0, G_OPTION_ARG_NONE, &opt_no_navigation,
      "Hide the change stepper, for a document with no changes", NULL },
    { "no-sandbox", 0, 0, G_OPTION_ARG_NONE, &opt_no_sandbox,
      "Run WebKit without its content sandbox", NULL },
    { "check-sandbox", 0, 0, G_OPTION_ARG_NONE, &opt_check_sandbox,
      "Report whether the sandbox can start here, and exit", NULL },
    /* Absent, this is one process showing one document, which is what git
       difftool needs: it runs the tool once per file and waits for each to
       exit.  Given, the invocation instead joins -- or becomes -- the single
       instance with this id, and md-view accumulates documents in one place.
       The id is what keeps the two apart: md-diff and md-view register
       different names, so neither can ever adopt the other's windows. */
    { "session", 0, 0, G_OPTION_ARG_FILENAME, &opt_session,
      "Join or become the single instance with this application id", "ID" },
    { G_OPTION_REMAINING, 0, 0, G_OPTION_ARG_FILENAME_ARRAY, &opt_files,
      NULL, "[FILE]" },
    { NULL, 0, 0, 0, NULL, NULL, NULL }
};

/* --- Input --- */

static GString *read_document(const char *path, GError **error)
{
    if (path == NULL || g_str_equal(path, "-")) {
        GString *buffer = g_string_new(NULL);
        char chunk[8192];
        size_t count;

        while ((count = fread(chunk, 1, sizeof chunk, stdin)) > 0)
            g_string_append_len(buffer, chunk, count);

        if (ferror(stdin)) {
            g_set_error(error, G_FILE_ERROR, g_file_error_from_errno(errno),
                        "reading stdin: %s", g_strerror(errno));
            g_string_free(buffer, TRUE);
            return NULL;
        }
        return buffer;
    }

    char *contents;
    gsize length;
    if (!g_file_get_contents(path, &contents, &length, error))
        return NULL;

    GString *buffer = g_string_new_len(contents, length);
    g_free(contents);
    return buffer;
}

/* A session invocation's document arrives down the invoking process's own
   stdin: GApplication passes the file descriptor to the primary instance, so
   an embedded-image document costs nothing to hand over and never touches
   the filesystem or the bus. */
static GString *read_remote_document(GApplicationCommandLine *cmdline,
                                     const char *path, GError **error)
{
    if (path != NULL && !g_str_equal(path, "-")) {
        /* Relative to the directory the invoking process was in, which is
           not the primary instance's. */
        char *full = g_path_is_absolute(path)
            ? g_strdup(path)
            : g_build_filename(g_application_command_line_get_cwd(cmdline),
                               path, NULL);
        GString *document = read_document(full, error);
        g_free(full);
        return document;
    }

    GInputStream *stream = g_application_command_line_get_stdin(cmdline);
    if (stream == NULL) {
        g_set_error(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                    "no document: nothing on stdin");
        return NULL;
    }

    GString *buffer = g_string_new(NULL);
    char chunk[8192];
    gssize count;
    while ((count = g_input_stream_read(stream, chunk, sizeof chunk,
                                        NULL, error)) > 0)
        g_string_append_len(buffer, chunk, count);
    g_object_unref(stream);

    if (count < 0) {
        g_string_free(buffer, TRUE);
        return NULL;
    }
    return buffer;
}

/* --- Sandbox --- */

/* Probe with WebKit's own sandbox helper rather than reimplementing what it
   does.  bwrap inherits this process's AppArmor label, so the answer
   accounts for the profile -- which the sysctl cannot, being global -- and
   it fails in exactly the place WebKit would ("setting up uid map:
   Permission denied").  Hand-rolling the unshare and the uid-map write gets
   a different answer from the real thing: the kernel refuses a self-written
   map that bwrap, which writes its child's from the parent, is allowed. */
static gboolean userns_available(gboolean verbose)
{
    char *command[] = { "bwrap", "--unshare-user", "--ro-bind", "/", "/",
                        "/bin/true", NULL };
    char *diagnostic = NULL;
    GError *error = NULL;
    int status = 0;

    if (!g_spawn_sync(NULL, command, NULL, G_SPAWN_SEARCH_PATH, NULL, NULL,
                      NULL, &diagnostic, &status, &error)) {
        /* No bwrap to ask -- WebKit brings its own answer soon enough. */
        if (verbose)
            g_print("  bwrap: %s\n", error->message);
        g_clear_error(&error);
        return TRUE;
    }

    gboolean available = g_spawn_check_wait_status(status, NULL);
    if (verbose) {
        char *label = NULL;
        g_file_get_contents("/proc/self/attr/current", &label, NULL, NULL);
        g_print("  AppArmor label: %s", label != NULL ? label : "?\n");
        g_print("  bwrap --unshare-user: %s", available ? "ok\n" : "refused\n");
        if (!available && diagnostic != NULL && *diagnostic != '\0')
            g_print("  %s", diagnostic);
        g_free(label);
    }
    g_free(diagnostic);
    return available;
}

/* --- JavaScript bridge --- */

/* Both scripts return the caption for their counter, so the only thing the
   reply is wanted for is the label passed through as user data. */
static void on_js_finished(GObject *source, GAsyncResult *result, gpointer data)
{
    GtkWidget *counter = data;  /* NULL when there is nothing to update */
    GError *error = NULL;
    JSCValue *value = webkit_web_view_evaluate_javascript_finish(
        WEBKIT_WEB_VIEW(source), result, &error);

    if (value == NULL) {
        g_clear_error(&error);
        return;
    }
    if (counter != NULL && jsc_value_is_string(value)) {
        char *text = jsc_value_to_string(value);
        gtk_label_set_label(GTK_LABEL(counter), text);
        g_free(text);
    }
    g_object_unref(value);
}

/* Token replace rather than printf: the scripts contain a JS modulo. */
static void run_js(Viewer *viewer, const char *source, const char *call,
                   GtkWidget *counter)
{
    char **parts = g_strsplit(source, "__CALL__", -1);
    char *script = g_strjoinv(call, parts);

    webkit_web_view_evaluate_javascript(viewer->webview, script, -1, NULL, NULL,
                                        NULL, on_js_finished, counter);
    g_free(script);
    g_strfreev(parts);
}

static void navigate(Viewer *viewer, int delta)
{
    char *call = g_strdup_printf("go(%d)", delta);
    run_js(viewer, NAV_JS, call, viewer->win->counter);
    g_free(call);
}

/* --- Search --- */

/* Quote a UTF-8 string as a JavaScript string literal, so that a query can
   be pasted into the call without any of it being read as code.  U+2028 and
   U+2029 are spelled out because JavaScript parsed them as line terminators
   until ES2019, and the WebKit in front of us may be older. */
static char *quote_js(const char *text)
{
    GString *out = g_string_new("\"");

    for (const guchar *p = (const guchar *) text; *p != '\0'; p++) {
        if (p[0] == 0xE2 && p[1] == 0x80 && (p[2] == 0xA8 || p[2] == 0xA9)) {
            g_string_append_printf(out, "\\u202%c", p[2] == 0xA8 ? '8' : '9');
            p += 2;
        } else if (*p == '"' || *p == '\\') {
            g_string_append_c(out, '\\');
            g_string_append_c(out, (char) *p);
        } else if (*p < 0x20 || *p == 0x7F) {
            g_string_append_printf(out, "\\u%04x", *p);
        } else {
            g_string_append_c(out, (char) *p);
        }
    }

    g_string_append_c(out, '"');
    return g_string_free(out, FALSE);
}

/* Highlight every occurrence of `query` and jump to the first. */
static void find_text(Viewer *viewer, const char *query)
{
    char *literal = quote_js(query);
    char *call = g_strdup_printf("search(%s)", literal);

    run_js(viewer, FIND_JS, call, viewer->win->search_counter);
    g_free(call);
    g_free(literal);
}

/* Step to the next (+1) or previous (-1) occurrence, wrapping around. */
static void find_step(Viewer *viewer, int delta)
{
    char *call = g_strdup_printf("go(%d)", delta);
    run_js(viewer, FIND_JS, call, viewer->win->search_counter);
    g_free(call);
}

static void open_search(Window *win)
{
    gtk_search_bar_set_search_mode(GTK_SEARCH_BAR(win->search_bar), TRUE);
    gtk_widget_grab_focus(win->search_entry);
    /* Reopening on a query already in the box replaces it as you type,
       the way a second Ctrl+F does everywhere else. */
    gtk_editable_select_region(GTK_EDITABLE(win->search_entry), 0, -1);
}

static void close_search(Window *win)
{
    gtk_search_bar_set_search_mode(GTK_SEARCH_BAR(win->search_bar), FALSE);
}

/* True while the keystroke belongs to the search box rather than the page. */
static gboolean search_focused(Window *win)
{
    GtkWidget *focus = gtk_window_get_focus(GTK_WINDOW(win->window));

    return focus != NULL
        && (focus == win->search_bar
            || gtk_widget_is_ancestor(focus, win->search_bar));
}

static void on_search_changed(GtkSearchEntry *entry, gpointer data)
{
    Viewer *viewer = current_viewer(data);

    if (viewer != NULL)
        find_text(viewer, gtk_editable_get_text(GTK_EDITABLE(entry)));
}

static void on_search_step_clicked(GtkButton *button, gpointer data)
{
    Viewer *viewer = current_viewer(data);

    if (viewer != NULL)
        find_step(viewer, GPOINTER_TO_INT(
                      g_object_get_data(G_OBJECT(button), "delta")));
}

/* Closing the bar -- by Escape or by its own close button -- has to drop the
   highlights with it, and hand the keyboard back to the page. */
static void on_search_mode(GObject *bar, GParamSpec *spec, gpointer data)
{
    Window *win = data;
    Viewer *viewer = current_viewer(win);
    (void) spec;

    if (gtk_search_bar_get_search_mode(GTK_SEARCH_BAR(bar)))
        return;
    gtk_editable_set_text(GTK_EDITABLE(win->search_entry), "");
    gtk_label_set_label(GTK_LABEL(win->search_counter), "");
    if (viewer == NULL)
        return;
    find_text(viewer, "");
    gtk_widget_grab_focus(GTK_WIDGET(viewer->webview));
}

static void on_navigate_clicked(GtkButton *button, gpointer data)
{
    Viewer *viewer = current_viewer(data);

    if (viewer != NULL)
        navigate(viewer, GPOINTER_TO_INT(
                     g_object_get_data(G_OBJECT(button), "delta")));
}

static void on_load_changed(WebKitWebView *webview, WebKitLoadEvent event,
                            gpointer data)
{
    Viewer *viewer = data;
    (void) webview;

    /* Called even without the stepper: the first evaluation is what builds
       the scrollbar map. */
    if (event == WEBKIT_LOAD_FINISHED) {
        viewer->loaded = TRUE;
        /* A document loading in a tab behind this one still needs the
           evaluation, which is what builds its scrollbar map -- but the
           counter it would report belongs to the tab in front. */
        run_js(viewer, NAV_JS, "label()",
               viewer == current_viewer(viewer->win) ? viewer->win->counter
                                                     : NULL);
    }
}

/* --- Hardening --- */

/* The document is the only thing this window ever shows.  A link in it must
   not be able to navigate the view somewhere else, and nothing may open a
   second one. */
static gboolean on_decide_policy(WebKitWebView *webview,
                                 WebKitPolicyDecision *decision,
                                 WebKitPolicyDecisionType type, gpointer data)
{
    Viewer *viewer = data;
    (void) webview;

    switch (type) {
    case WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION:
        if (!viewer->loaded)
            return FALSE;  /* the document itself */
        webkit_policy_decision_ignore(decision);
        return TRUE;
    case WEBKIT_POLICY_DECISION_TYPE_NEW_WINDOW_ACTION:
        webkit_policy_decision_ignore(decision);
        return TRUE;
    default:
        return FALSE;
    }
}

static WebKitWebView *build_webview(void)
{
    /* Ephemeral session with a proxy pointing nowhere.  Rendering a local
       document needs no network, and a document that asks for one is either
       tracking who opened it or carrying something out. */
    WebKitNetworkSession *session = webkit_network_session_new_ephemeral();
    WebKitNetworkProxySettings *proxy =
        webkit_network_proxy_settings_new("http://127.0.0.1:1/", NULL);
    webkit_network_session_set_proxy_settings(
        session, WEBKIT_NETWORK_PROXY_MODE_CUSTOM, proxy);
    webkit_network_proxy_settings_free(proxy);

    WebKitWebView *webview = g_object_new(WEBKIT_TYPE_WEB_VIEW,
                                          "network-session", session, NULL);
    g_object_unref(session);

    WebKitSettings *settings = webkit_web_view_get_settings(webview);
    /* The document is untrusted -- pandoc passes raw HTML in a markdown file
       straight through.  Disabling *markup* rather than JavaScript outright
       stops <script> and inline handlers while leaving
       webkit_web_view_evaluate_javascript working, which is what drives the
       stepper and the map. */
    webkit_settings_set_enable_javascript_markup(settings, FALSE);
    webkit_settings_set_enable_webgl(settings, FALSE);
    webkit_settings_set_enable_media(settings, FALSE);
    webkit_settings_set_enable_html5_local_storage(settings, FALSE);
    webkit_settings_set_enable_html5_database(settings, FALSE);

    return webview;
}

/* --- Input handling --- */

static gboolean on_key(GtkEventControllerKey *controller, guint keyval,
                       guint keycode, GdkModifierType state, gpointer data)
{
    Window *win = data;
    Viewer *viewer = current_viewer(win);
    gboolean ctrl = (state & GDK_CONTROL_MASK) != 0;
    gboolean shift = (state & GDK_SHIFT_MASK) != 0;
    gboolean alt = (state & GDK_ALT_MASK) != 0;
    const char *name = gdk_keyval_name(keyval);

    (void) controller;
    (void) keycode;
    if (name == NULL || viewer == NULL)
        return FALSE;

    /* Ctrl+F and Ctrl+S both open the search bar, from the page or from
       inside the bar itself.  Ctrl+S is here because it is what an Emacs
       hand reaches for; nothing in this window saves anything. */
    if (ctrl && (g_ascii_strcasecmp(name, "f") == 0
                 || g_ascii_strcasecmp(name, "s") == 0)) {
        open_search(win);
        return TRUE;
    }

    /* While the query is being typed the page keeps none of its bindings:
       "n" and "q" are letters here, not commands. */
    if (search_focused(win)) {
        if (g_str_equal(name, "Escape"))
            close_search(win);
        else if (g_str_equal(name, "Down")
                 || ((g_str_equal(name, "Return")
                      || g_str_equal(name, "KP_Enter")) && !shift))
            find_step(viewer, 1);
        else if (g_str_equal(name, "Up")
                 || ((g_str_equal(name, "Return")
                      || g_str_equal(name, "KP_Enter")) && shift))
            find_step(viewer, -1);
        else
            return FALSE;  /* the entry gets everything else, typing included */
        return TRUE;
    }

    if (g_str_equal(name, "Escape")
        && gtk_search_bar_get_search_mode(GTK_SEARCH_BAR(win->search_bar))) {
        /* A search left open with the page focused: the first Escape puts
           it away, a second closes the document. */
        close_search(win);
    } else if (g_str_equal(name, "Escape") || g_str_equal(name, "q")
        || (ctrl && g_str_equal(name, "w"))) {
        /* Closes the document in front.  With one open -- every diff, and
           md-view until a second arrives -- that is the window, which is
           what these keys have always done. */
        close_tab(win);
    } else if (ctrl && shift && g_ascii_strcasecmp(name, "b") == 0) {
        toggle_tabs(win);
    } else if (ctrl && shift && g_ascii_strcasecmp(name, "d") == 0) {
        detach_tab(win);
    } else if (ctrl && (g_str_equal(name, "Page_Down")
                        || g_str_equal(name, "Next"))) {
        step_tab(win, 1);
    } else if (ctrl && (g_str_equal(name, "Page_Up")
                        || g_str_equal(name, "Prior"))) {
        step_tab(win, -1);
    } else if (alt && name[0] >= '1' && name[0] <= '9' && name[1] == '\0') {
        gtk_notebook_set_current_page(GTK_NOTEBOOK(win->notebook),
                                      name[0] - '1');
    } else if (viewer->navigation
               && (g_str_equal(name, "n")
                   || (g_str_equal(name, "Tab") && !shift)
                   || (alt && g_str_equal(name, "Down")))) {
        navigate(viewer, 1);
    } else if (viewer->navigation
               && (g_str_equal(name, "p")
                   || g_str_equal(name, "ISO_Left_Tab")
                   || (alt && g_str_equal(name, "Up")))) {
        navigate(viewer, -1);
    } else if (ctrl && (g_str_equal(name, "plus") || g_str_equal(name, "equal")
                        || g_str_equal(name, "KP_Add"))) {
        webkit_web_view_set_zoom_level(
            viewer->webview, webkit_web_view_get_zoom_level(viewer->webview) * 1.1);
    } else if (ctrl && (g_str_equal(name, "minus")
                        || g_str_equal(name, "KP_Subtract"))) {
        webkit_web_view_set_zoom_level(
            viewer->webview, webkit_web_view_get_zoom_level(viewer->webview) / 1.1);
    } else if (ctrl && g_str_equal(name, "0")) {
        webkit_web_view_set_zoom_level(viewer->webview, 1.0);
    } else {
        return FALSE;
    }
    return TRUE;
}

/* --- Window --- */

/* Two-line header title (GTK4 has no Adwaita WindowTitle). */
static GtkWidget *build_title(const char *primary, const char *secondary)
{
    GtkWidget *top = gtk_label_new(primary);
    gtk_label_set_ellipsize(GTK_LABEL(top), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(top, "title");

    if (secondary == NULL || *secondary == '\0')
        return top;

    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *bottom = gtk_label_new(secondary);
    gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
    gtk_label_set_ellipsize(GTK_LABEL(bottom), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(bottom, "subtitle");
    gtk_widget_add_css_class(bottom, "dim-label");
    gtk_box_append(GTK_BOX(box), top);
    gtk_box_append(GTK_BOX(box), bottom);
    return box;
}

/* --- Tabs --- */

static Viewer *viewer_of(GtkWidget *page)
{
    return page != NULL ? g_object_get_data(G_OBJECT(page), "viewer") : NULL;
}

static Viewer *current_viewer(Window *win)
{
    int index = gtk_notebook_get_current_page(GTK_NOTEBOOK(win->notebook));

    if (index < 0)
        return NULL;
    return viewer_of(gtk_notebook_get_nth_page(GTK_NOTEBOOK(win->notebook),
                                               index));
}

/* Point the window's chrome at one document.  Everything here is per-tab
   but drawn once, so switching tabs means re-pointing rather than swapping
   widgets in and out. */
static void sync_chrome(Window *win, Viewer *viewer)
{
    if (viewer == NULL)
        return;

    gtk_window_set_title(GTK_WINDOW(win->window), viewer->title);
    gtk_header_bar_set_title_widget(
        GTK_HEADER_BAR(win->header),
        build_title(viewer->heading, viewer->subheading));

    /* A single file has no changes to step through; a diff does.  Which
       means the stepper comes and goes with the tab, rather than being
       settled once when the window is built. */
    gtk_widget_set_visible(win->stepper, viewer->navigation);
    gtk_widget_set_visible(win->counter, viewer->navigation);
    if (viewer->navigation)
        run_js(viewer, NAV_JS, "label()", win->counter);

    /* An open query follows you from document to document: the bar belongs
       to the window, so leaving it pointed at the tab you just left would
       show a count for something no longer on screen. */
    if (gtk_search_bar_get_search_mode(GTK_SEARCH_BAR(win->search_bar)))
        find_text(viewer,
                  gtk_editable_get_text(GTK_EDITABLE(win->search_entry)));
}

/* One document -- every diff, and md-view until a second arrives -- looks
   exactly as it did before there were tabs at all, unless the strip has been
   pinned to move a document between windows. */
static void update_tabs(Window *win)
{
    gtk_notebook_set_show_tabs(
        GTK_NOTEBOOK(win->notebook),
        tabs_pinned
        || gtk_notebook_get_n_pages(GTK_NOTEBOOK(win->notebook)) > 1);
}

/* Every window at once: a tab needs a strip to leave from and a strip to
   land on, and those are two different windows. */
static void refresh_tabs(GtkApplication *app)
{
    tabs_syncing = TRUE;
    for (GList *l = gtk_application_get_windows(app); l != NULL; l = l->next) {
        Window *win = g_object_get_data(G_OBJECT(l->data), "md-window");

        if (win == NULL)
            continue;
        update_tabs(win);
        if (win->tabs_button != NULL)
            gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(win->tabs_button),
                                         tabs_pinned);
    }
    tabs_syncing = FALSE;
}

static void on_tabs_toggled(GtkToggleButton *button, gpointer data)
{
    Window *win = data;

    /* Set by refresh_tabs on every other window; only the click counts. */
    if (tabs_syncing)
        return;
    tabs_pinned = gtk_toggle_button_get_active(button);
    refresh_tabs(gtk_window_get_application(GTK_WINDOW(win->window)));
}

static void toggle_tabs(Window *win)
{
    if (win->tabs_button == NULL)
        return;
    gtk_toggle_button_set_active(
        GTK_TOGGLE_BUTTON(win->tabs_button),
        !gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(win->tabs_button)));
}

static void on_switch_page(GtkNotebook *notebook, GtkWidget *page,
                           guint index, gpointer data)
{
    (void) notebook;
    (void) index;
    /* The page argument, not get_current_page(): the notebook has not
       finished switching while this runs. */
    sync_chrome(data, viewer_of(page));
}

/* A page can arrive by being dragged out of another window, in which case
   the document is still pointed at the chrome it was using a moment ago --
   which may be on its way out, its last tab having just left. */
static void on_page_added(GtkNotebook *notebook, GtkWidget *child,
                          guint index, gpointer data)
{
    Window *win = data;
    Viewer *viewer = viewer_of(child);

    (void) notebook;
    (void) index;
    if (viewer != NULL)
        viewer->win = win;
    update_tabs(win);
    sync_chrome(win, current_viewer(win));
}

static void on_page_removed(GtkNotebook *notebook, GtkWidget *child,
                            guint index, gpointer data)
{
    Window *win = data;

    (void) notebook;
    (void) child;
    (void) index;
    /* Tearing the window down removes every page on the way out; that is
       not the last tab being closed. */
    if (win->closing)
        return;
    if (gtk_notebook_get_n_pages(GTK_NOTEBOOK(win->notebook)) == 0) {
        gtk_window_destroy(GTK_WINDOW(win->window));
        return;
    }
    update_tabs(win);
    sync_chrome(win, current_viewer(win));
}

static void remove_tab(Window *win, GtkWidget *page)
{
    int index = gtk_notebook_page_num(GTK_NOTEBOOK(win->notebook), page);

    /* The last document goes out with the window, so that q and Ctrl+W
       still close a single-document window the way they always have. */
    if (gtk_notebook_get_n_pages(GTK_NOTEBOOK(win->notebook)) <= 1) {
        gtk_window_close(GTK_WINDOW(win->window));
        return;
    }
    if (index >= 0)
        gtk_notebook_remove_page(GTK_NOTEBOOK(win->notebook), index);
}

static void close_tab(Window *win)
{
    int index = gtk_notebook_get_current_page(GTK_NOTEBOOK(win->notebook));

    if (index < 0) {
        gtk_window_close(GTK_WINDOW(win->window));
        return;
    }
    remove_tab(win, gtk_notebook_get_nth_page(GTK_NOTEBOOK(win->notebook),
                                              index));
}

static void step_tab(Window *win, int delta)
{
    int count = gtk_notebook_get_n_pages(GTK_NOTEBOOK(win->notebook));
    int index = gtk_notebook_get_current_page(GTK_NOTEBOOK(win->notebook));

    if (count <= 1 || index < 0)
        return;
    gtk_notebook_set_current_page(GTK_NOTEBOOK(win->notebook),
                                  ((index + delta) % count + count) % count);
}

/* A tab dropped on the desktop.  GTK asks for a notebook to put it in, and
   moves the page itself; all that is needed is somewhere for it to land. */
static GtkNotebook *on_create_window(GtkNotebook *notebook, GtkWidget *page,
                                     gpointer data)
{
    Window *win = data;
    Window *fresh;

    (void) notebook;
    (void) page;
    fresh = window_new(gtk_window_get_application(GTK_WINDOW(win->window)));
    gtk_window_present(GTK_WINDOW(fresh->window));
    return GTK_NOTEBOOK(fresh->notebook);
}

/* The same move without the mouse.  Removing a page drops the notebook's
   reference, and the document goes with it, so the page is held across the
   move rather than removed and re-created. */
static void detach_tab(Window *win)
{
    GtkNotebook *notebook = GTK_NOTEBOOK(win->notebook);
    int index = gtk_notebook_get_current_page(notebook);
    GtkWidget *page;
    Viewer *viewer;
    Window *fresh;

    /* A document already alone in its window has nowhere to go. */
    if (index < 0 || gtk_notebook_get_n_pages(notebook) <= 1)
        return;

    page = gtk_notebook_get_nth_page(notebook, index);
    viewer = viewer_of(page);
    if (viewer == NULL)
        return;

    fresh = window_new(gtk_window_get_application(GTK_WINDOW(win->window)));
    g_object_ref(page);
    gtk_notebook_detach_tab(notebook, page);
    gtk_notebook_append_page(GTK_NOTEBOOK(fresh->notebook), page,
                             build_tab_label(page, viewer));
    gtk_notebook_set_tab_reorderable(GTK_NOTEBOOK(fresh->notebook), page, TRUE);
    gtk_notebook_set_tab_detachable(GTK_NOTEBOOK(fresh->notebook), page, TRUE);
    g_object_unref(page);

    gtk_window_present(GTK_WINDOW(fresh->window));
}

static void on_tab_close_clicked(GtkButton *button, gpointer data)
{
    GtkWidget *page = data;
    Viewer *viewer = viewer_of(page);

    (void) button;
    if (viewer != NULL)
        remove_tab(viewer->win, page);
}

static GtkWidget *build_tab_label(GtkWidget *page, Viewer *viewer)
{
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    GtkWidget *label = gtk_label_new(viewer->title);
    GtkWidget *close = gtk_button_new_from_icon_name("window-close-symbolic");

    /* Ellipsizing gives a label a minimum width of nothing, so without a
       floor the tab strip shrinks every name to a bare "...". */
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_label_set_width_chars(GTK_LABEL(label), 12);
    gtk_label_set_max_width_chars(GTK_LABEL(label), 24);
    gtk_box_append(GTK_BOX(box), label);

    gtk_button_set_has_frame(GTK_BUTTON(close), FALSE);
    gtk_widget_set_tooltip_text(close, "Close document (Ctrl+W)");
    g_signal_connect(close, "clicked", G_CALLBACK(on_tab_close_clicked), page);
    gtk_box_append(GTK_BOX(box), close);

    /* Two files of the same name from different directories are otherwise
       indistinguishable once the tab strip has ellipsized them. */
    if (viewer->subheading != NULL && *viewer->subheading != '\0')
        gtk_widget_set_tooltip_text(label, viewer->subheading);
    return box;
}

/* Only in a session: a diff has one document and nowhere to move it to. */
static void add_tabs_button(GtkWidget *header, Window *win)
{
    GtkWidget *button = gtk_toggle_button_new();

    gtk_button_set_icon_name(GTK_BUTTON(button), "view-list-symbolic");
    gtk_widget_set_tooltip_text(button,
                                "Show the tab bar, to drag a document to "
                                "another window (Ctrl+Shift+B)");
    /* Set before the handler is connected, so a window opened while the
       strip is pinned adopts the state without re-announcing it. */
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(button), tabs_pinned);
    g_signal_connect(button, "toggled", G_CALLBACK(on_tabs_toggled), win);
    gtk_header_bar_pack_start(GTK_HEADER_BAR(header), button);
    win->tabs_button = button;
}

static void add_stepper(GtkWidget *header, Window *win)
{
    static const struct {
        const char *icon;
        int delta;
        const char *tip;
    } BUTTONS[] = {
        { "go-up-symbolic", -1, "Previous change (p / Shift+Tab / Alt+Up)" },
        { "go-down-symbolic", 1, "Next change (n / Tab / Alt+Down)" },
    };

    /* Built whichever kind of document opens first, and shown or hidden per
       tab: one window can hold a diff and a file side by side. */
    win->stepper = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(win->stepper, "linked");

    for (gsize i = 0; i < G_N_ELEMENTS(BUTTONS); i++) {
        GtkWidget *button = gtk_button_new_from_icon_name(BUTTONS[i].icon);
        gtk_widget_set_tooltip_text(button, BUTTONS[i].tip);
        g_object_set_data(G_OBJECT(button), "delta",
                          GINT_TO_POINTER(BUTTONS[i].delta));
        g_signal_connect(button, "clicked", G_CALLBACK(on_navigate_clicked),
                         win);
        gtk_box_append(GTK_BOX(win->stepper), button);
    }
    gtk_header_bar_pack_start(GTK_HEADER_BAR(header), win->stepper);

    win->counter = gtk_label_new("…");
    gtk_widget_add_css_class(win->counter, "dim-label");
    gtk_header_bar_pack_end(GTK_HEADER_BAR(header), win->counter);
}

/* The search bar sits under the header bar and slides out of the way when
   it is not in use, so a document that is only ever read never pays for it.
   Unlike the stepper it is never hidden: a single file has no changes to
   step through, but it has words to look for. */
static void add_search_bar(GtkWidget *content, Window *win)
{
    static const struct {
        const char *icon;
        int delta;
        const char *tip;
    } BUTTONS[] = {
        { "go-up-symbolic", -1, "Previous match (Up / Shift+Return)" },
        { "go-down-symbolic", 1, "Next match (Down / Return)" },
    };

    GtkWidget *bar = gtk_search_bar_new();
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    GtkWidget *entry = gtk_search_entry_new();

    gtk_widget_set_hexpand(entry, TRUE);
    gtk_widget_set_size_request(entry, 260, -1);
    /* Not the placeholder-text property: that arrived in GTK 4.10, and
       bookworm ships 4.8. */
    gtk_widget_set_tooltip_text(entry, "Find in document (Ctrl+F / Ctrl+S)");
    g_signal_connect(entry, "search-changed", G_CALLBACK(on_search_changed),
                     win);
    gtk_box_append(GTK_BOX(row), entry);

    /* Wide enough for "no matches" so the buttons beside it hold still as
       the count changes under the typing. */
    win->search_counter = gtk_label_new("");
    gtk_label_set_width_chars(GTK_LABEL(win->search_counter), 11);
    gtk_label_set_xalign(GTK_LABEL(win->search_counter), 1.0);
    gtk_widget_add_css_class(win->search_counter, "dim-label");
    gtk_box_append(GTK_BOX(row), win->search_counter);

    for (gsize i = 0; i < G_N_ELEMENTS(BUTTONS); i++) {
        GtkWidget *button = gtk_button_new_from_icon_name(BUTTONS[i].icon);
        gtk_widget_set_tooltip_text(button, BUTTONS[i].tip);
        g_object_set_data(G_OBJECT(button), "delta",
                          GINT_TO_POINTER(BUTTONS[i].delta));
        g_signal_connect(button, "clicked",
                         G_CALLBACK(on_search_step_clicked), win);
        gtk_box_append(GTK_BOX(row), button);
    }

    gtk_search_bar_set_child(GTK_SEARCH_BAR(bar), row);
    gtk_search_bar_connect_entry(GTK_SEARCH_BAR(bar), GTK_EDITABLE(entry));
    gtk_search_bar_set_show_close_button(GTK_SEARCH_BAR(bar), TRUE);

    /* Recorded before the handler that reads them is connected. */
    win->search_bar = bar;
    win->search_entry = entry;
    g_signal_connect(bar, "notify::search-mode-enabled",
                     G_CALLBACK(on_search_mode), win);
    gtk_box_append(GTK_BOX(content), bar);
}

/* Takes the document; copies the captions, which belong to whichever
   invocation is being parsed and are reset before the next one. */
static Viewer *viewer_new(GString *document)
{
    Viewer *viewer = g_new0(Viewer, 1);
    viewer->document = document;
    viewer->title = g_strdup(opt_title != NULL ? opt_title : "md-diff");
    viewer->heading = g_strdup(opt_heading != NULL ? opt_heading
                                                  : viewer->title);
    viewer->subheading = g_strdup(opt_subheading);
    viewer->navigation = !opt_no_navigation;
    return viewer;
}

static void viewer_free(Viewer *viewer)
{
    g_string_free(viewer->document, TRUE);
    g_free(viewer->title);
    g_free(viewer->heading);
    g_free(viewer->subheading);
    g_free(viewer);
}

static void add_tab(Window *win, Viewer *viewer)
{
    viewer->win = win;
    viewer->webview = build_webview();
    g_signal_connect(viewer->webview, "load-changed",
                     G_CALLBACK(on_load_changed), viewer);
    g_signal_connect(viewer->webview, "decide-policy",
                     G_CALLBACK(on_decide_policy), viewer);
    webkit_web_view_load_html(viewer->webview, viewer->document->str, NULL);

    GtkWidget *page = GTK_WIDGET(viewer->webview);
    gtk_widget_set_vexpand(page, TRUE);
    /* The page owns the document: closing the tab is what frees it, whether
       that happens by hand or by the window going away. */
    g_object_set_data_full(G_OBJECT(page), "viewer", viewer,
                           (GDestroyNotify) viewer_free);

    int index = gtk_notebook_append_page(GTK_NOTEBOOK(win->notebook), page,
                                         build_tab_label(page, viewer));
    gtk_notebook_set_tab_reorderable(GTK_NOTEBOOK(win->notebook), page, TRUE);
    /* Only in a session.  A diff has one document and one window, and git is
       waiting on the process: there is nothing to drag it to. */
    if (session_mode)
        gtk_notebook_set_tab_detachable(GTK_NOTEBOOK(win->notebook), page,
                                        TRUE);
    update_tabs(win);
    gtk_notebook_set_current_page(GTK_NOTEBOOK(win->notebook), index);
    sync_chrome(win, viewer);
}

static void on_window_destroy(GtkWidget *widget, gpointer data)
{
    Window *win = data;

    (void) widget;
    win->closing = TRUE;
}

static Window *window_new(GtkApplication *app)
{
    /* X11 draws the titlebar and taskbar icon from the _NET_WM_ICON pixmaps
       on the window, and GTK4 only attaches them when the icon is named --
       the application id alone leaves the window with the generic default.
       The name resolves through the icon theme to the SVG that
       packaging/install-desktop.sh installs.  It follows the id in use, so a
       session names its own icon rather than borrowing the diff's. */
    gtk_window_set_default_icon_name(
        g_application_get_application_id(G_APPLICATION(app)));

    Window *win = g_new0(Window, 1);
    win->window = gtk_application_window_new(app);
    gtk_window_set_default_size(GTK_WINDOW(win->window), 1100, 850);
    /* Outlives the destroy handler, which still reads it. */
    g_object_set_data_full(G_OBJECT(win->window), "md-window", win, g_free);
    g_signal_connect(win->window, "destroy", G_CALLBACK(on_window_destroy),
                     win);

    win->header = gtk_header_bar_new();
    if (session_mode)
        add_tabs_button(win->header, win);
    add_stepper(win->header, win);
    gtk_window_set_titlebar(GTK_WINDOW(win->window), win->header);

    win->notebook = gtk_notebook_new();
    gtk_notebook_set_scrollable(GTK_NOTEBOOK(win->notebook), TRUE);
    gtk_notebook_set_show_border(GTK_NOTEBOOK(win->notebook), FALSE);
    gtk_notebook_set_show_tabs(GTK_NOTEBOOK(win->notebook), FALSE);
    /* The group name is what lets a tab be dragged from one of these windows
       into another: GTK will only drop a tab into a notebook sharing it. */
    if (session_mode)
        gtk_notebook_set_group_name(GTK_NOTEBOOK(win->notebook), "md-view");
    g_signal_connect(win->notebook, "create-window",
                     G_CALLBACK(on_create_window), win);
    g_signal_connect(win->notebook, "page-added", G_CALLBACK(on_page_added),
                     win);
    g_signal_connect(win->notebook, "switch-page", G_CALLBACK(on_switch_page),
                     win);
    g_signal_connect(win->notebook, "page-removed", G_CALLBACK(on_page_removed),
                     win);

    GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    add_search_bar(content, win);
    gtk_widget_set_vexpand(win->notebook, TRUE);
    gtk_box_append(GTK_BOX(content), win->notebook);
    gtk_window_set_child(GTK_WINDOW(win->window), content);

    GtkEventController *keys = gtk_event_controller_key_new();
    g_signal_connect(keys, "key-pressed", G_CALLBACK(on_key), win);
    /* CAPTURE, not the default BUBBLE: the WebView holds focus and claims
       arrow keys for scrolling, so Alt+Up/Down would never reach a
       bubble-phase handler.  Keys we don't bind still return FALSE here and
       propagate on, leaving plain Up/Down scrolling intact. */
    gtk_event_controller_set_propagation_phase(keys, GTK_PHASE_CAPTURE);
    gtk_widget_add_controller(win->window, keys);

    return win;
}

/* The window a joining document should open in: the one most recently
   focused, so a tab lands where the eye already is. */
static Window *session_window(GtkApplication *app)
{
    GList *windows = gtk_application_get_windows(app);

    return windows != NULL
        ? g_object_get_data(G_OBJECT(windows->data), "md-window") : NULL;
}

/* Undocking, exposed as an application action as well as a key.  It is the
   one window operation with no other way in: a document can be opened from a
   shell and closed from its tab, but only a drag could move it -- and a drag
   is exactly what a script, or a desktop shortcut, cannot do. */
static void on_detach_action(GSimpleAction *action, GVariant *parameter,
                             gpointer data)
{
    GtkApplication *app = data;
    GtkWindow *active = gtk_application_get_active_window(app);
    Window *win = active != NULL
        ? g_object_get_data(G_OBJECT(active), "md-window") : NULL;

    (void) action;
    (void) parameter;
    if (win == NULL)
        win = session_window(app);
    if (win != NULL)
        detach_tab(win);
}

static void on_toggle_tabs_action(GSimpleAction *action, GVariant *parameter,
                                  gpointer data)
{
    GtkApplication *app = data;
    GtkWindow *active = gtk_application_get_active_window(app);
    Window *win = active != NULL
        ? g_object_get_data(G_OBJECT(active), "md-window") : NULL;

    (void) action;
    (void) parameter;
    if (win == NULL)
        win = session_window(app);
    if (win != NULL)
        toggle_tabs(win);
}

static const GActionEntry ACTIONS[] = {
    { "detach-tab", on_detach_action, NULL, NULL, NULL, { 0 } },
    { "toggle-tabs", on_toggle_tabs_action, NULL, NULL, NULL, { 0 } },
};

/* One process, one document: the document was read before the application
   started, because there is only ever this one. */
static void on_activate(GtkApplication *app, gpointer data)
{
    Window *win = window_new(app);

    add_tab(win, data);
    gtk_window_present(GTK_WINDOW(win->window));
}

/* Discard the previous invocation's captions.  The option variables are
   parse targets, and in session mode they are parsed once per invocation --
   what a window keeps is the copy viewer_new takes. */
static void reset_options(void)
{
    g_clear_pointer(&opt_title, g_free);
    g_clear_pointer(&opt_heading, g_free);
    g_clear_pointer(&opt_subheading, g_free);
    g_clear_pointer(&opt_files, g_strfreev);
    g_clear_pointer(&opt_session, g_free);
    opt_no_navigation = FALSE;
}

/* A session invocation, local or remote: parse its arguments, take its
   document, give it a window.  Returning ends the invoking process, which is
   why md-view hands back the shell prompt instead of waiting -- except for
   the invocation that became the primary instance, which stays for as long
   as it has a window to show. */
static int on_command_line(GApplication *app, GApplicationCommandLine *cmdline,
                           gpointer data)
{
    GError *error = NULL;
    char **arguments = g_application_command_line_get_arguments(cmdline, NULL);
    GOptionContext *context = g_option_context_new(NULL);

    (void) data;
    reset_options();
    g_option_context_add_main_entries(context, OPTIONS, NULL);
    if (!g_option_context_parse_strv(context, &arguments, &error)) {
        g_application_command_line_printerr(cmdline, "md-diff-view: %s\n",
                                            error->message);
        g_clear_error(&error);
        g_option_context_free(context);
        g_strfreev(arguments);
        return 2;
    }
    g_option_context_free(context);
    g_strfreev(arguments);

    GString *document = read_remote_document(
        cmdline, opt_files != NULL ? opt_files[0] : NULL, &error);
    if (document == NULL) {
        g_application_command_line_printerr(cmdline, "md-diff-view: %s\n",
                                            error->message);
        g_clear_error(&error);
        return 1;
    }

    /* Joins the window already up, if there is one.  A document opening
       behind the others would be no use, so the window comes forward. */
    Window *win = session_window(GTK_APPLICATION(app));
    if (win == NULL)
        win = window_new(GTK_APPLICATION(app));
    add_tab(win, viewer_new(document));
    gtk_window_present(GTK_WINDOW(win->window));
    return 0;
}

int main(int argc, char **argv)
{
    GError *error = NULL;
    GOptionContext *context = g_option_context_new(
        "- show a rendered HTML document in a window");

    g_option_context_add_main_entries(context, OPTIONS, NULL);
    /* parse_strv, not parse: the plain parser converts arguments from the
       locale encoding, so a title carrying the "old → new" arrow fails with
       "invalid byte sequence" under a non-UTF-8 locale -- which is what git
       difftool tends to hand us.  Arguments here are UTF-8 by construction. */
    char **arguments = g_strdupv(argv);
    if (!g_option_context_parse_strv(context, &arguments, &error)) {
        g_printerr("md-diff-view: %s\n", error->message);
        g_clear_error(&error);
        g_strfreev(arguments);
        g_option_context_free(context);
        return 2;
    }
    g_strfreev(arguments);
    g_option_context_free(context);

    if (opt_check_sandbox) {
        gboolean available = userns_available(TRUE);
        g_print("sandbox: %s\n", available ? "available" : "unavailable");
        return available ? 0 : 1;
    }

    if (opt_no_sandbox) {
        /* Must be set before WebKit spawns its web process. */
        g_setenv(SANDBOX_ENV, "1", TRUE);
        g_printerr("Warning: running WebKit without its content sandbox\n");
    } else if (!userns_available(FALSE)) {
        g_printerr("%s", SANDBOX_HELP);
        return 1;
    }

    session_mode = opt_session != NULL;
    if (session_mode) {
        /* Nothing is read here.  The document travels as a file descriptor
           to whichever instance shows it, and that may be a process which
           started long before this one -- so the reading belongs there, in
           on_command_line, not in the invocation that happens to be typing.

           --no-sandbox is likewise the primary's decision: it is acted on
           before WebKit starts, and by the time a later invocation arrives
           the renderer it would have configured is already running. */
        GtkApplication *app = gtk_application_new(
            opt_session, G_APPLICATION_HANDLES_COMMAND_LINE);
        g_signal_connect(app, "command-line", G_CALLBACK(on_command_line),
                         NULL);
        g_action_map_add_action_entries(G_ACTION_MAP(app), ACTIONS,
                                        G_N_ELEMENTS(ACTIONS), app);
        int status = g_application_run(G_APPLICATION(app), argc, argv);
        g_object_unref(app);
        return status;
    }

    GString *document = read_document(opt_files != NULL ? opt_files[0] : NULL,
                                      &error);
    if (document == NULL) {
        g_printerr("md-diff-view: %s\n", error->message);
        g_clear_error(&error);
        return 1;
    }

    /* NON_UNIQUE: git difftool invokes us once per file and waits for each to
       exit.  Sharing one instance would let later invocations return
       immediately and break that sequencing -- which is exactly what
       --session asks for, and exactly why the diff never passes it. */
    GtkApplication *app = gtk_application_new(APP_ID,
                                              G_APPLICATION_NON_UNIQUE);
    g_signal_connect(app, "activate", G_CALLBACK(on_activate),
                     viewer_new(document));
    int status = g_application_run(G_APPLICATION(app), 0, NULL);

    g_object_unref(app);
    return status;
}
