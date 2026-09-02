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

typedef struct {
    GtkWidget *counter;      /* NULL when the stepper is off */
    GtkWidget *search_bar;
    GtkWidget *search_entry;
    GtkWidget *search_counter;
    WebKitWebView *webview;
    GString *document;
    gboolean navigation;
    gboolean loaded;         /* first load done: later navigation is denied */
} Viewer;

static char *opt_title = NULL;
static char *opt_heading = NULL;
static char *opt_subheading = NULL;
static gboolean opt_no_navigation = FALSE;
static gboolean opt_no_sandbox = FALSE;
static gboolean opt_check_sandbox = FALSE;
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
    run_js(viewer, NAV_JS, call, viewer->counter);
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

    run_js(viewer, FIND_JS, call, viewer->search_counter);
    g_free(call);
    g_free(literal);
}

/* Step to the next (+1) or previous (-1) occurrence, wrapping around. */
static void find_step(Viewer *viewer, int delta)
{
    char *call = g_strdup_printf("go(%d)", delta);
    run_js(viewer, FIND_JS, call, viewer->search_counter);
    g_free(call);
}

static void open_search(Viewer *viewer)
{
    gtk_search_bar_set_search_mode(GTK_SEARCH_BAR(viewer->search_bar), TRUE);
    gtk_widget_grab_focus(viewer->search_entry);
    /* Reopening on a query already in the box replaces it as you type,
       the way a second Ctrl+F does everywhere else. */
    gtk_editable_select_region(GTK_EDITABLE(viewer->search_entry), 0, -1);
}

static void close_search(Viewer *viewer)
{
    gtk_search_bar_set_search_mode(GTK_SEARCH_BAR(viewer->search_bar), FALSE);
}

/* True while the keystroke belongs to the search box rather than the page. */
static gboolean search_focused(Viewer *viewer, GtkWidget *window)
{
    GtkWidget *focus = gtk_window_get_focus(GTK_WINDOW(window));

    return focus != NULL
        && (focus == viewer->search_bar
            || gtk_widget_is_ancestor(focus, viewer->search_bar));
}

static void on_search_changed(GtkSearchEntry *entry, gpointer data)
{
    find_text(data, gtk_editable_get_text(GTK_EDITABLE(entry)));
}

static void on_search_step_clicked(GtkButton *button, gpointer data)
{
    find_step(data, GPOINTER_TO_INT(
                  g_object_get_data(G_OBJECT(button), "delta")));
}

/* Closing the bar -- by Escape or by its own close button -- has to drop the
   highlights with it, and hand the keyboard back to the page. */
static void on_search_mode(GObject *bar, GParamSpec *spec, gpointer data)
{
    Viewer *viewer = data;
    (void) spec;

    if (gtk_search_bar_get_search_mode(GTK_SEARCH_BAR(bar)))
        return;
    gtk_editable_set_text(GTK_EDITABLE(viewer->search_entry), "");
    gtk_label_set_label(GTK_LABEL(viewer->search_counter), "");
    find_text(viewer, "");
    gtk_widget_grab_focus(GTK_WIDGET(viewer->webview));
}

static void on_navigate_clicked(GtkButton *button, gpointer data)
{
    Viewer *viewer = data;
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
        run_js(viewer, NAV_JS, "label()", viewer->counter);
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
    Viewer *viewer = data;
    gboolean ctrl = (state & GDK_CONTROL_MASK) != 0;
    gboolean shift = (state & GDK_SHIFT_MASK) != 0;
    gboolean alt = (state & GDK_ALT_MASK) != 0;
    const char *name = gdk_keyval_name(keyval);
    GtkWidget *window = gtk_event_controller_get_widget(
        GTK_EVENT_CONTROLLER(controller));

    (void) keycode;
    if (name == NULL)
        return FALSE;

    /* Ctrl+F and Ctrl+S both open the search bar, from the page or from
       inside the bar itself.  Ctrl+S is here because it is what an Emacs
       hand reaches for; nothing in this window saves anything. */
    if (ctrl && (g_ascii_strcasecmp(name, "f") == 0
                 || g_ascii_strcasecmp(name, "s") == 0)) {
        open_search(viewer);
        return TRUE;
    }

    /* While the query is being typed the page keeps none of its bindings:
       "n" and "q" are letters here, not commands. */
    if (search_focused(viewer, window)) {
        if (g_str_equal(name, "Escape"))
            close_search(viewer);
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
        && gtk_search_bar_get_search_mode(GTK_SEARCH_BAR(viewer->search_bar))) {
        /* A search left open with the page focused: the first Escape puts
           it away, a second closes the window. */
        close_search(viewer);
    } else if (g_str_equal(name, "Escape") || g_str_equal(name, "q")
        || (ctrl && g_str_equal(name, "w"))) {
        gtk_window_close(GTK_WINDOW(window));
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

static void add_stepper(GtkWidget *header, Viewer *viewer)
{
    static const struct {
        const char *icon;
        int delta;
        const char *tip;
    } BUTTONS[] = {
        { "go-up-symbolic", -1, "Previous change (p / Shift+Tab / Alt+Up)" },
        { "go-down-symbolic", 1, "Next change (n / Tab / Alt+Down)" },
    };

    for (gsize i = 0; i < G_N_ELEMENTS(BUTTONS); i++) {
        GtkWidget *button = gtk_button_new_from_icon_name(BUTTONS[i].icon);
        gtk_widget_set_tooltip_text(button, BUTTONS[i].tip);
        g_object_set_data(G_OBJECT(button), "delta",
                          GINT_TO_POINTER(BUTTONS[i].delta));
        g_signal_connect(button, "clicked", G_CALLBACK(on_navigate_clicked),
                         viewer);
        gtk_header_bar_pack_start(GTK_HEADER_BAR(header), button);
    }

    viewer->counter = gtk_label_new("…");
    gtk_widget_add_css_class(viewer->counter, "dim-label");
    gtk_header_bar_pack_end(GTK_HEADER_BAR(header), viewer->counter);
}

/* The search bar sits under the header bar and slides out of the way when
   it is not in use, so a document that is only ever read never pays for it.
   Unlike the stepper it is built whichever mode we are in: a single file has
   no changes to step through, but it has words to look for. */
static void add_search_bar(GtkWidget *content, Viewer *viewer)
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
                     viewer);
    gtk_box_append(GTK_BOX(row), entry);

    /* Wide enough for "no matches" so the buttons beside it hold still as
       the count changes under the typing. */
    viewer->search_counter = gtk_label_new("");
    gtk_label_set_width_chars(GTK_LABEL(viewer->search_counter), 11);
    gtk_label_set_xalign(GTK_LABEL(viewer->search_counter), 1.0);
    gtk_widget_add_css_class(viewer->search_counter, "dim-label");
    gtk_box_append(GTK_BOX(row), viewer->search_counter);

    for (gsize i = 0; i < G_N_ELEMENTS(BUTTONS); i++) {
        GtkWidget *button = gtk_button_new_from_icon_name(BUTTONS[i].icon);
        gtk_widget_set_tooltip_text(button, BUTTONS[i].tip);
        g_object_set_data(G_OBJECT(button), "delta",
                          GINT_TO_POINTER(BUTTONS[i].delta));
        g_signal_connect(button, "clicked",
                         G_CALLBACK(on_search_step_clicked), viewer);
        gtk_box_append(GTK_BOX(row), button);
    }

    gtk_search_bar_set_child(GTK_SEARCH_BAR(bar), row);
    gtk_search_bar_connect_entry(GTK_SEARCH_BAR(bar), GTK_EDITABLE(entry));
    gtk_search_bar_set_show_close_button(GTK_SEARCH_BAR(bar), TRUE);

    /* Recorded before the handler that reads them is connected. */
    viewer->search_bar = bar;
    viewer->search_entry = entry;
    g_signal_connect(bar, "notify::search-mode-enabled",
                     G_CALLBACK(on_search_mode), viewer);
    gtk_box_append(GTK_BOX(content), bar);
}

static void on_activate(GtkApplication *app, gpointer data)
{
    Viewer *viewer = data;
    const char *title = opt_title != NULL ? opt_title : "md-diff";

    /* X11 draws the titlebar and taskbar icon from the _NET_WM_ICON pixmaps
       on the window, and GTK4 only attaches them when the icon is named --
       the application id alone leaves the window with the generic default.
       The name resolves through the icon theme to the SVG that
       packaging/install-desktop.sh installs. */
    gtk_window_set_default_icon_name(APP_ID);

    GtkWidget *window = gtk_application_window_new(app);
    gtk_window_set_title(GTK_WINDOW(window), title);
    gtk_window_set_default_size(GTK_WINDOW(window), 1100, 850);

    GtkWidget *header = gtk_header_bar_new();
    gtk_header_bar_set_title_widget(
        GTK_HEADER_BAR(header),
        build_title(opt_heading != NULL ? opt_heading : title, opt_subheading));
    if (viewer->navigation)
        add_stepper(header, viewer);
    gtk_window_set_titlebar(GTK_WINDOW(window), header);

    viewer->webview = build_webview();
    g_signal_connect(viewer->webview, "load-changed",
                     G_CALLBACK(on_load_changed), viewer);
    g_signal_connect(viewer->webview, "decide-policy",
                     G_CALLBACK(on_decide_policy), viewer);
    webkit_web_view_load_html(viewer->webview, viewer->document->str, NULL);

    GtkWidget *content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    add_search_bar(content, viewer);
    gtk_widget_set_vexpand(GTK_WIDGET(viewer->webview), TRUE);
    gtk_box_append(GTK_BOX(content), GTK_WIDGET(viewer->webview));
    gtk_window_set_child(GTK_WINDOW(window), content);

    GtkEventController *keys = gtk_event_controller_key_new();
    g_signal_connect(keys, "key-pressed", G_CALLBACK(on_key), viewer);
    /* CAPTURE, not the default BUBBLE: the WebView holds focus and claims
       arrow keys for scrolling, so Alt+Up/Down would never reach a
       bubble-phase handler.  Keys we don't bind still return FALSE here and
       propagate on, leaving plain Up/Down scrolling intact. */
    gtk_event_controller_set_propagation_phase(keys, GTK_PHASE_CAPTURE);
    gtk_widget_add_controller(window, keys);

    gtk_window_present(GTK_WINDOW(window));
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

    Viewer viewer = { NULL, NULL, NULL, NULL, NULL, NULL,
                      !opt_no_navigation, FALSE };
    viewer.document = read_document(opt_files != NULL ? opt_files[0] : NULL,
                                    &error);
    if (viewer.document == NULL) {
        g_printerr("md-diff-view: %s\n", error->message);
        g_clear_error(&error);
        return 1;
    }

    /* NON_UNIQUE: git difftool invokes us once per file and waits for each to
       exit.  Sharing one instance would let later invocations return
       immediately and break that sequencing. */
    GtkApplication *app = gtk_application_new(APP_ID,
                                              G_APPLICATION_NON_UNIQUE);
    g_signal_connect(app, "activate", G_CALLBACK(on_activate), &viewer);
    int status = g_application_run(G_APPLICATION(app), 0, NULL);

    g_object_unref(app);
    g_string_free(viewer.document, TRUE);
    return status;
}
