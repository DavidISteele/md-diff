/* Minimal GtkNotebook tab-detach reproducer -- no WebKit, no md-view code.
 *
 * Six tabs of plain labels, detachable, with the smallest create-window
 * handler that can work.  If dragging a tab out and then switching tabs
 * misdraws the strip here, the fault is GTK's and not the application's.
 *
 *   cc -O2 -Wall $(pkg-config --cflags gtk4) -o notebook-repro \
 *      notebook-repro.c $(pkg-config --libs gtk4)
 */
#include <gtk/gtk.h>

static GtkWidget *make_window(GtkApplication *app, gboolean fill);

static GtkNotebook *on_create_window(GtkNotebook *notebook, GtkWidget *page,
                                     gpointer data)
{
    GtkWidget *window;
    (void) notebook; (void) page;
    window = make_window(GTK_APPLICATION(data), FALSE);
    gtk_window_present(GTK_WINDOW(window));
    return GTK_NOTEBOOK(g_object_get_data(G_OBJECT(window), "notebook"));
}

static GtkWidget *make_window(GtkApplication *app, gboolean fill)
{
    GtkWidget *window = gtk_application_window_new(app);
    GtkWidget *notebook = gtk_notebook_new();

    gtk_window_set_default_size(GTK_WINDOW(window), 900, 600);
    gtk_notebook_set_group_name(GTK_NOTEBOOK(notebook), "repro");
    g_signal_connect(notebook, "create-window",
                     G_CALLBACK(on_create_window), app);
    g_object_set_data(G_OBJECT(window), "notebook", notebook);
    gtk_window_set_child(GTK_WINDOW(window), notebook);

    if (fill) {
        for (int i = 1; i <= 6; i++) {
            char name[32];
            g_snprintf(name, sizeof name, "document-%d.md", i);
            GtkWidget *body = gtk_label_new(name);
            GtkWidget *tab = gtk_label_new(name);

            gtk_notebook_append_page(GTK_NOTEBOOK(notebook), body, tab);
            gtk_notebook_set_tab_detachable(GTK_NOTEBOOK(notebook), body, TRUE);
            gtk_notebook_set_tab_reorderable(GTK_NOTEBOOK(notebook), body, TRUE);
        }
    }
    return window;
}

static void activate(GtkApplication *app, gpointer data)
{
    (void) data;
    gtk_window_present(GTK_WINDOW(make_window(app, TRUE)));
}

int main(int argc, char **argv)
{
    GtkApplication *app = gtk_application_new("org.user.local.notebook-repro",
                                              G_APPLICATION_NON_UNIQUE);
    g_signal_connect(app, "activate", G_CALLBACK(activate), NULL);
    int status = g_application_run(G_APPLICATION(app), argc, argv);
    g_object_unref(app);
    return status;
}
