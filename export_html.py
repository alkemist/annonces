import base64
import json
import os
from datetime import datetime
from html import escape
import pandas as pd

print('Start export_html.py')

def show_links(val):
    """Transforme une chaîne de texte représentant une URL en lien HTML cliquable."""
    if isinstance(val, str) and (
            val.startswith("http://") or val.startswith("https://")
    ):
        return f'<a href="{val}" target="_blank" rel="noopener noreferrer">link</a>'
    return val

def export_csv(
        fichiers_csv,
        fichier_html_sortie="index.html",
        colonnes_select=None,
        colonnes_input=None,
        colonnes_nowrap=None,
        auto_select_max_unique=10,
):
    if colonnes_select is None:
        colonnes_select = []
    if colonnes_input is None:
        colonnes_input = []
    if colonnes_nowrap is None:
        colonnes_nowrap = []

    categories = []
    for category_index, (category_name, tabs_dict) in enumerate(fichiers_csv.items()):
        categories.append({
            "name": category_name,
            "slug": f"category-{category_index}",
            "tabs": [
                {
                    "tab_name": tab_name,
                    "path": tab_config["path"] if isinstance(tab_config, dict) else tab_config,
                    "type": tab_config.get("type", "csv") if isinstance(tab_config, dict) else "csv",
                    "groups": tab_config.get("groups", []) if isinstance(tab_config, dict) else [],
                    "highlight_error_columns": tab_config.get("highlight_error_columns", []) if isinstance(tab_config, dict) else [],
                    "default_filters": tab_config.get("default_filters", []) if isinstance(tab_config, dict) else {},
                    "default_columns_hidden": tab_config.get("default_columns_hidden", []) if isinstance(tab_config, dict) else [],
                }
                for tab_name, tab_config in tabs_dict.items()
            ],
        })

    oldest_csv_mtime = None

    table_index = 0
    for category in categories:
        prepared_tabs = []
        for tab in category["tabs"]:
            nom_onglet = tab["tab_name"]
            chemin_csv = tab["path"]

            if os.path.exists(chemin_csv):
                if tab["type"] == "csv":
                    mtime = os.path.getmtime(chemin_csv) + 2 * 3600  # Compense le décalage horaire du serveur
                    if oldest_csv_mtime is None or mtime < oldest_csv_mtime:
                        oldest_csv_mtime = mtime

            else:
                print(f"Error: Le fichier '{chemin_csv}' n'existe pas. L'onglet '{nom_onglet}' sera ignoré.")
                continue

            if tab["type"] == "image":
                with open(chemin_csv, "rb") as image:
                    image_base64 = base64.b64encode(image.read()).decode("ascii")
                prepared_tabs.append({
                    "tab_name": nom_onglet,
                    "tab_id": f"tab-{table_index}",
                    "image_html": (
                        f'<img class="deployment-image" src="data:image/png;base64,{image_base64}" '
                        f'alt="{escape(nom_onglet, quote=True)}">'
                    ),
                })
                table_index += 1
                continue

            cols = tab.get("groups", [])

            df = (
                pd.read_csv(chemin_csv, dtype=str)
                .fillna("")
                .replace({
                    "True": "✅",
                    "False": "❌",
                    "active": "✅",
                    "closed": "❌",
                    "success": "✅",
                    "failed": "❌",
                })
            )

            df = df.map(show_links)

            table_id = f"tableau-{table_index}"
            cols_groupe_attr = json.dumps(cols).replace('"', "&quot;")
            highlight_error_cols = tab.get("highlight_error_columns", [])
            highlight_error_cols_attr = json.dumps(highlight_error_cols).replace('"', "&quot;")
            default_filters = tab.get("default_filters", {})
            default_filters_attr = json.dumps(default_filters).replace('"', "&quot;")
            default_columns_hidden = tab.get("default_columns_hidden", [])
            default_columns_hidden_attr = json.dumps(default_columns_hidden).replace('"', "&quot;")

            table_html = df.to_html(
                table_id=table_id,
                index=False,
                classes="display compact cell-border",
                escape=False,
            )

            prepared_tabs.append({
                "tab_name": nom_onglet,
                "tab_id": f"tab-{table_index}",
                "table_html": table_html,
                "group_cols_attr": cols_groupe_attr,
                "highlight_error_cols_attr": highlight_error_cols_attr,
                "default_filters": default_filters_attr,
                "default_columns_hidden": default_columns_hidden_attr,
            })
            table_index += 1

        category["tabs"] = prepared_tabs

    categories_nav_html = []
    categories_content_html = []

    for category_index, category in enumerate(categories):
        category_active_class = "active" if category_index == 0 else ""
        categories_nav_html.append(
            f'<button class="tab-btn category-tab-btn {category_active_class}" onclick="openCategoryTab(event, \'{category["slug"]}\')">{category["name"]}</button>'
        )

        sub_tabs_nav_html = []
        sub_tabs_content_html = []
        for tab_index, tab in enumerate(category["tabs"]):
            tab_active_class = "active" if tab_index == 0 else ""
            sub_tabs_nav_html.append(
                f'<button class="tab-btn sub-tab-btn {tab_active_class}" onclick="openSubTab(event, \'{category["slug"]}\', \'{tab["tab_id"]}\')">{tab["tab_name"]}</button>'
            )
            if "image_html" in tab:
                sub_tabs_content_html.append(
                    f"""
            <div id="{tab["tab_id"]}" class="sub-tab-content {tab_active_class}" data-category="{category["slug"]}">
                <div class="image-wrapper">{tab["image_html"]}</div>
            </div>
            """
                )
                continue

            sub_tabs_content_html.append(
                f"""
            <div id="{tab["tab_id"]}" class="sub-tab-content {tab_active_class}" data-category="{category["slug"]}" data-default-columns-hidden="{tab["default_columns_hidden"]}">
                <div class="col-toggle-bar">
                    <button type="button" class="col-toggle-btn">Colonnes ▾</button>
                    <div class="col-toggle-menu"></div>
                </div>
                <div class="table-wrapper" data-group-cols="{tab["group_cols_attr"]}" data-highlight-error-cols="{tab["highlight_error_cols_attr"]}" data-default-filters="{tab["default_filters"]}">
                    {tab["table_html"]}
                </div>
            </div>
            """
            )

        categories_content_html.append(
            f"""
        <div id="{category["slug"]}" class="tab-content {category_active_class}">
            <div class="tabs-nav tabs-nav-secondary">
                {' '.join(sub_tabs_nav_html)}
            </div>
            {''.join(sub_tabs_content_html)}
        </div>
        """
        )

    nav_str = "\n".join(categories_nav_html)
    content_str = "\n".join(categories_content_html)

    cols_select_js = json.dumps(colonnes_select)
    cols_input_js = json.dumps(colonnes_input)
    cols_nowrap_js = json.dumps(colonnes_nowrap)

    date_str = datetime.fromtimestamp(oldest_csv_mtime).strftime("%d/%m à %H:%M")
    page_title = f"Rapport au {date_str}"

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>{page_title}</title>
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
    <link rel="stylesheet" href="https://cdn.datatables.net/rowgroup/1.4.0/css/rowGroup.dataTables.min.css">
    <style>
        html, body, .container {{ height: 100%; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; background-color: #f8f9fa; color: #333; }}
        .container {{
            display: flex;
            flex-direction: column;
            margin: 0 auto;
            background: #fff;
            padding: 0 1rem;
            border-radius: 0;
        }}

        .tabs-nav {{ display: flex; flex-wrap: wrap; flex-shrink: 0; border-bottom: 2px solid #dee2e6; margin-bottom: 0.5rem; gap: 0.5rem; }}
        .tabs-nav-secondary {{ border-bottom-width: 1px; }}
        .tab-btn {{
            padding: 0.6rem 1.2rem;
            border: none;
            background: none;
            font-size: 0.8rem;
            font-weight: 500;
            color: #6c757d;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.2s ease;
        }}
        .tab-btn:hover {{ color: #0d6efd; }}
        .tab-btn.active {{ color: #0d6efd; border-bottom-color: #0d6efd; font-weight: 600; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{
            display: flex;
            flex-direction: column;
            flex: 1;
            min-height: 0;
        }}
        .sub-tab-btn {{
            font-size: 0.75rem;
            padding: 0.45rem 1rem;
        }}
        .sub-tab-content {{ display: none; }}
        .sub-tab-content.active {{
            display: flex;
            flex-direction: column;
            flex: 1;
            min-height: 0;
        }}
        .image-wrapper, .table-wrapper {{
            flex: 1;
            min-height: 0;
            overflow: auto;
        }}
        .deployment-image {{
            display: block;
            max-width: 100%;
            height: auto;
            margin: 0 auto;
        }}

        table.dataTable th,
        table.dataTable td {{
            text-align: left !important;
        }}

        table.dataTable td a {{ color: #0d6efd; text-decoration: none; }}
        table.dataTable td a:hover {{ text-decoration: underline; }}

        tr.dtrg-level-0 th {{
            background-color: #96b6d6 !important;
            color: #212529 !important;
            text-align: left !important;
            font-size: 0.95rem;
            font-weight: bold;
            padding: 8px 12px !important;
            border-top: 2px solid #ced4da !important;
        }}
        tr.dtrg-level-1 th {{
            background-color: #c4d6e7 !important;
            color: #495057 !important;
            text-align: left !important;
            font-size: 0.9rem;
            font-weight: 600;
            padding-left: 28px !important;
        }}
        tr.dtrg-level-2 th {{
            background-color: #c4d6e7c9 !important;
            color: #6c757d !important;
            text-align: left !important;
            font-size: 0.85rem;
            padding-left: 44px !important;
        }}
        tr.dtrg-level-3 th {{
            background-color: #c4d6e79c !important;
            color: #6c757d !important;
            text-align: left !important;
            font-size: 0.85rem;
            padding-left: 60px !important;
        }}

        .col-nowrap {{
            white-space: nowrap !important;
        }}

        table.dataTable tbody tr.has-error > * {{
            background-color: rgba(220, 53, 69, 0.35) !important;
        }}

        .col-toggle-bar {{
            position: relative;
            display: flex;
            flex-shrink: 0;
            justify-content: flex-end;
            margin-bottom: 0.4rem;
        }}
        .col-toggle-btn {{
            padding: 0.35rem 0.8rem;
            font-size: 0.8rem;
            border: 1px solid #ced4da;
            border-radius: 4px;
            background: #fff;
            color: #495057;
            cursor: pointer;
        }}
        .col-toggle-btn:hover {{ background: #f1f3f5; }}
        .col-toggle-menu {{
            display: none;
            position: absolute;
            top: 100%;
            right: 0;
            margin-top: 4px;
            background: #fff;
            border: 1px solid #ced4da;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
            padding: 0.5rem;
            z-index: 10;
            max-height: 300px;
            overflow-y: auto;
            min-width: 200px;
        }}
        .col-toggle-menu.open {{ display: block; }}
        .col-toggle-menu label {{
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.82rem;
            padding: 0.15rem 0.3rem;
            white-space: nowrap;
            cursor: pointer;
        }}
        .col-toggle-menu label:hover {{ background: #f1f3f5; }}

        .filter-multiselect {{
            position: relative;
            width: 100%;
        }}
        .filter-multiselect-btn {{
            width: 100%;
            padding: 4px 6px;
            box-sizing: border-box;
            font-size: 0.82rem;
            border: 1px solid #ccc;
            border-radius: 4px;
            background-color: #fff;
            color: #495057;
            text-align: left;
            cursor: pointer;
            white-space: nowrap !important;
        }}
        .filter-multiselect-menu {{
            display: none;
            position: absolute;
            top: 100%;
            left: 0;
            min-width: 100%;
            max-height: 240px;
            overflow-y: auto;
            box-sizing: border-box;
            margin-top: 2px;
            padding: 0.25rem;
            background: #fff;
            border: 1px solid #ced4da;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
            z-index: 20;
        }}
        .filter-multiselect-menu.open {{ display: block; }}
        .filter-multiselect-menu label {{
            display: flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.2rem 0.3rem;
            font-size: 0.82rem;
            white-space: nowrap;
            cursor: pointer;
        }}
        .filter-multiselect-menu label:hover {{ background: #f1f3f5; }}
        .filter-multiselect-menu input[type="checkbox"] {{
            width: auto;
            margin: 0;
        }}
        thead input:not([type="checkbox"]) {{
            width: 100%;
            padding: 4px 6px;
            box-sizing: border-box;
            font-size: 0.82rem;
            border: 1px solid #ccc;
            border-radius: 4px;
            background-color: #fff;
        }}
        table.dataTable {{ width: 100% !important; margin-top: 10px !important; }}
        table.dataTable thead th {{
            position: sticky !important;
            -webkit-position: sticky !important;
            background: #fff;
            z-index: 2;
        }}
        table.dataTable thead tr:first-child th {{
            top: 0;
            z-index: 4;
        }}
        table.dataTable thead tr.filters th {{
            top: 30px;
            z-index: 3;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="tabs-nav">
            {nav_str}
        </div>
        {content_str}
    </div>

    <!-- JS jQuery, DataTables & RowGroup CDN -->
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/rowgroup/1.4.0/js/dataTables.rowGroup.min.js"></script>
    <script>
        const explicitSelectCols = {cols_select_js};
        const explicitInputCols = {cols_input_js};
        const explicitNowrapCols = {cols_nowrap_js};
        const autoSelectMaxUnique = {auto_select_max_unique};

        function openCategoryTab(evt, tabId) {{
            $('.tab-content').removeClass('active');
            $('.category-tab-btn').removeClass('active');
            $('#' + tabId).addClass('active');
            $(evt.currentTarget).addClass('active');
            $.fn.dataTable.tables({{ visible: true, api: true }}).columns.adjust();
        }}

        function openSubTab(evt, categoryId, tabId) {{
            $('.sub-tab-content[data-category="' + categoryId + '"]').removeClass('active');
            $('#' + categoryId + ' .sub-tab-btn').removeClass('active');
            $('#' + tabId).addClass('active');
            $(evt.currentTarget).addClass('active');
            $.fn.dataTable.tables({{ visible: true, api: true }}).columns.adjust();
        }}

        $(document).on('click', function() {{
            $('.col-toggle-menu').removeClass('open');
            $('.filter-multiselect-menu').removeClass('open');
        }});

        $(document).ready(function() {{
            $('table[id^="tableau-"]').each(function() {{
                var $table = $(this);
                var $wrapper = $table.closest('.table-wrapper');
                var targetCols = $wrapper.data('group-cols') || [];
                var highlightErrorCols = $wrapper.data('highlight-error-cols') || [];
                var defaultFilters = $wrapper.data('default-filters') || {{}};
                var defaultColumnsHidden = $wrapper.closest('.sub-tab-content').data('default-columns-hidden') || [];

                // 1. Duplication de l'en-tête pour les filtres
                $table.find('thead tr').clone(true).addClass('filters').appendTo($table.find('thead'));

                // 2. Identification des en-têtes et indices
                var groupIndices = [];
                var highlightErrorIndices = [];
                var headerTitles = [];
                var columnDefsConfig = [];

                $table.find('thead tr:first th').each(function(index) {{
                    var title = $(this).text().trim();
                    headerTitles[index] = title;

                    // Application dynamique de la classe `col-nowrap` si spécifiée dans colonnes_nowrap
                    if (explicitNowrapCols.includes(title)) {{
                        columnDefsConfig.push({{
                            targets: index,
                            className: 'col-nowrap'
                        }});
                    }}
                }});

                targetCols.forEach(function(colName) {{
                    var idx = headerTitles.indexOf(colName);
                    if (idx !== -1) {{
                        groupIndices.push(idx);
                    }}
                }});

                highlightErrorCols.forEach(function(colName) {{
                    var idx = headerTitles.indexOf(colName);
                    if (idx !== -1) {{
                        highlightErrorIndices.push(idx);
                    }}
                }});

                // 3. Configuration DataTables
                var dtConfig = {{
                    orderCellsTop: true,
                    columnDefs: columnDefsConfig,
                    rowCallback: function(row) {{
                        var hasError = highlightErrorIndices.some(function(idx) {{
                            return row.cells[idx].textContent.trim().includes('❌');
                        }});
                        $(row).toggleClass('has-error', hasError);
                    }},
                    language: {{
                        url: 'https://cdn.datatables.net/plug-ins/1.13.6/i18n/fr-FR.json'
                    }},
                    pageLength: 100,
                    initComplete: function () {{
                        var api = this.api();
                        var $toggleBar = $table.closest('.sub-tab-content').find('.col-toggle-bar');
                        var $toggleBtn = $toggleBar.find('.col-toggle-btn');
                        var $toggleMenu = $toggleBar.find('.col-toggle-menu');

                        api.columns().eq(0).each(function (colIdx) {{
                            var column = api.column(colIdx);
                            var title = headerTitles[colIdx] || ('Colonne ' + (colIdx + 1));
                            var isHidden = defaultColumnsHidden.includes(title);

                            $toggleMenu.append(
                                $('<label></label>')
                                    .append(
                                        $('<input type="checkbox" ' + (isHidden ? '' : 'checked') + ' />')
                                            .data('col-idx', colIdx)
                                            .on('change', function () {{
                                                var idx = $(this).data('col-idx');
                                                var $cells = $table.find('tr').find('td:nth-child(' + (idx + 1) + '), th:nth-child(' + (idx + 1) + ')');
                                                if (this.checked) {{
                                                    $cells.show();
                                                }} else {{
                                                    $cells.hide();
                                                }}
                                            }})
                                    )
                                    .append($('<span></span>').text(title))
                            );

                            if (isHidden) {{
                                var $cellsHidden = $table.find('tr').find('td:nth-child(' + (colIdx + 1) + '), th:nth-child(' + (colIdx + 1) + ')');
                                $cellsHidden.hide();
                            }}
                        }});

                        $toggleBtn.on('click', function (e) {{
                            e.stopPropagation();
                            $('.col-toggle-menu').not($toggleMenu).removeClass('open');
                            $toggleMenu.toggleClass('open');
                        }});
                        $toggleMenu.on('click', function (e) {{
                            e.stopPropagation();
                        }});

                        api.columns().eq(0).each(function (colIdx) {{
                            var column = api.column(colIdx);
                            var cell = $table.find('.filters th').eq($(column.header()).index());
                            var title = $(cell).text().trim();
                            var defaultFilterValue = defaultFilters[title] || '';
                            var defaultFilterValues = Array.isArray(defaultFilterValue)
                                ? defaultFilterValue
                                : (defaultFilterValue === '' ? [] : [defaultFilterValue]);
                            var defaultFilterOptions = defaultFilterValues.map(function(value) {{
                                return 'value:' + value;
                            }});

                            var uniqueValues = [];
                            column.data().unique().sort().each(function (d) {{
                                var text = $('<div>').html(d).text().trim();
                                if (text !== '' && !uniqueValues.includes(text)) {{
                                    uniqueValues.push(text);
                                }}
                            }});
                            uniqueValues.sort();

                            var forceInput = explicitInputCols.includes(title);
                            var useSelect = !forceInput && (
                                explicitSelectCols.includes(title) ||
                                (autoSelectMaxUnique > 0 && uniqueValues.length <= autoSelectMaxUnique)
                            );

                            if (useSelect) {{
                                var $container = $('<div class="filter-multiselect"></div>').appendTo($(cell).empty());
                                var $button = $('<button type="button" class="filter-multiselect-btn">Tous</button>').appendTo($container);
                                var $menu = $('<div class="filter-multiselect-menu"></div>').appendTo($container);
                                var filterOptions = [
                                    {{ value: '__EMPTY__', label: '(Vide)' }},
                                    {{ value: '__NOT_EMPTY__', label: '(Non vide)' }}
                                ];

                                $.each(uniqueValues, function (key, val) {{
                                    filterOptions.push({{ value: 'value:' + val, label: val }});
                                }});

                                function updateFilter() {{
                                    var selectedValues = $menu.find('input:checked').map(function() {{
                                        return $(this).val();
                                    }}).get();
                                    var searchTerms = selectedValues.map(function(value) {{
                                        if (value === '__EMPTY__') {{
                                            return '^\\\\s*$';
                                        }}
                                        if (value === '__NOT_EMPTY__') {{
                                            return '^(?=\\\\s*\\\\S).*$';
                                        }}
                                        var escapedValue = $.fn.dataTable.util.escapeRegex(value.slice(6));
                                        return '^' + escapedValue + '$';
                                    }});

                                    $button.text(selectedValues.length ? selectedValues.length + ' sélectionné(s)' : 'Tous');
                                    column.search(searchTerms.length ? searchTerms.join('|') : '', true, false).draw();
                                }}

                                $.each(filterOptions, function(key, option) {{
                                    var $checkbox = $('<input type="checkbox">').val(option.value);
                                    if (defaultFilterOptions.includes(option.value)) {{
                                        $checkbox.prop('checked', true);
                                    }}
                                    $('<label></label>')
                                        .append($checkbox)
                                        .append($('<span></span>').text(option.label))
                                        .appendTo($menu);
                                }});

                                $button.on('click', function(e) {{
                                    e.stopPropagation();
                                    $('.filter-multiselect-menu').not($menu).removeClass('open');
                                    $menu.toggleClass('open');
                                }});
                                $menu.on('click', function(e) {{
                                    e.stopPropagation();
                                }});
                                $menu.on('change', 'input[type="checkbox"]', updateFilter);

                                if (defaultFilterOptions.length > 0) {{
                                    updateFilter();
                                }}
                            }} else {{
                                $(cell).html('<input type="text" />');
                                $('input', cell).val(Array.isArray(defaultFilterValue) ? defaultFilterValue.join('|') : defaultFilterValue);
                                $('input', cell).off('keyup change').on('change keyup clear', function () {{
                                    if (column.search() !== this.value) {{
                                        column.search(this.value).draw();
                                    }}
                                }});

                                if (defaultFilterValue !== '') {{
                                  $('input', cell).trigger('change');
                                }}
                            }}
                        }});
                    }}
                }};

                // Activation du regroupement multi-colonnes
                if (groupIndices.length > 0) {{
                    dtConfig.order = groupIndices.map(function(idx) {{
                        return [idx, 'asc'];
                    }});

                    dtConfig.rowGroup = {{
                        dataSrc: groupIndices,
                        startRender: function (rows, group, level) {{
                            var cleanGroup = $('<div>').html(group).text() || '(Vide)';
                            var colName = headerTitles[groupIndices[level]] || '';
                            return colName + ' : ' + cleanGroup + ' (' + rows.count() + ')';
                        }}
                    }};
                }}

                $table.DataTable(dtConfig);
            }});
        }});
    </script>
</body>
</html>"""

    with open(fichier_html_sortie, "w", encoding="utf-8") as f:
        f.write(html_content)


# --- EXEMPLE D'UTILISATION ---

export_csv(
    {
      "Annonces": {
        "Tournus": {
          "path": "annonces_tournus.csv",
          "groups": ["meuble", "type"],
          "highlight_error_columns": [],
          "default_filters": {},
          "default_columns_hidden": ['caracteristiques', 'description'],
        },
        "Neuville": {
          "path": "annonces_neuville.csv",
          "groups": ["meuble", "type"],
          "highlight_error_columns": [],
          "default_filters": {},
          "default_columns_hidden": ['caracteristiques', 'description'],
        },
      },
    },
    "index.html",
    colonnes_select=[],
    colonnes_input=[],
    colonnes_nowrap=[],
)

print("Rapport HTML généré avec succès dans 'rapport.html'.")
