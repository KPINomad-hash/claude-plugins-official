# Документ правил Horizun (`horizun.cad-requirements/1`)

Справка составлена по `docs/DWG-TO-BIM.md` и исходному файлу `CadRequirementSet.cs` проекта
Horizun 4.0.0. Списки ключей и значений взяты из исходника. Примеры для стены и колонны — из
документации; остальные заготовки собраны из тех же ключей и **в Revit не проверялись**.
Начинай всегда с заготовки, которую возвращает `horizun_query_cad` в режиме профиля, а эту
справку используй, чтобы её заполнить.

Документ отвергается целиком при любой ошибке: неизвестный ключ, значение вне списка,
несовпадение единиц. Это задумано: частично понятый документ строил бы не то.

## Каркас

```jsonc
{
  "schema": "horizun.cad-requirements/1",
  "requirement_set": { "id": "tmfc-ar", "version": "1.0.0", "title": "АР, типовой этаж" },
  "source":     { "units": "millimeter" },
  "tolerances": { "point_mm": 1.0, "gap_mm": 25.0, "angle_degrees": 2.0, "arc_sagitta_mm": 5.0 },
  "rules": [ /* правила */ ]
}
```

Верхний уровень — ровно эти пять ключей. В `source` допустимы `units`, `case_sensitive_layers`,
`role`, `extent_mm`. Значение `units` должно совпадать с единицами, в которых Revit загрузил
связь.

## Ключи правила

`id`, `precedence`, `discipline`, `layers`, `exclude_layers`, `produces`, `category`,
`hosted_on`, `host_layers`, `host_faces`, `wall_types`, `mirror`, `mirror_variant_type`,
`family_type`, `system_type`, `level`, `base_level`, `top_level`, `phase`, `design_option`,
`geometry`, `height_mm`, `offset_mm`, `sill_height_mm`, `head_height_mm`, `thickness_mm`,
`diameter_mm`, `slope_percent`, `join_rule`, `min_confidence`, `structural`, `allow_structural`,
`naming`, `section`, `on_ambiguous`, `on_manual_divergence`, `parameters`.

- `layers` и `exclude_layers` — списки с подстановкой `*`.
- `precedence` — при споре двух правил за слой побеждает большее значение.
- `family_type` — «Семейство: Тип» точно так, как в проекте Revit.
- `level` — имя уровня. Элемент встаёт на уровень плюс `offset_mm`; координата Z из чертежа не
  используется.
- `structural` — несущий элемент. Не указывай `false` «на всякий случай»: без ключа действует
  значение по умолчанию самого документа.
- `allow_structural` — согласие человека на изменение несущего элемента (например, отверстие в
  плите). Это другой ключ, не путай с `structural`.

## `produces` — что строится

```
wall, curtain_wall, floor, ceiling, roof, room, room_separator,
column, structural_column, beam, brace, foundation, grid, level,
door, window, opening, wall_opening, shaft, stair, railing,
furniture, generic_model,
pipe, duct, conduit, cable_tray, pipe_accessory, duct_accessory,
air_terminal, plumbing_fixture, mechanical_equipment, electrical_fixture
```

Значение вне списка отвергается.

## `geometry.from` — как читать чертёж

| Значение | Что читает | Для чего |
| --- | --- | --- |
| `double_lines` | две параллельные линии на расстоянии толщины | прямые стены |
| `double_arcs` | две концентрические дуги | криволинейные стены |
| `closed_loops` | замкнутый контур, вложенные контуры — отверстия | перекрытия, потолки, кровли, помещения, проёмы, шахты, колонны по контуру |
| `single_lines` | одна линия | оси, балки, оси труб и воздуховодов, границы помещений |
| `point_clusters` | близко стоящие метки | двери, окна, колонны, оборудование |
| `blocks` | вхождения блоков | зарезервировано |

Другие ключи `geometry`: `min_thickness_mm`, `max_thickness_mm`, `min_overlap_mm`,
`min_overlap_fraction`, `min_length_mm`, `max_length_mm`, `min_area_mm2`, `max_area_mm2`,
`cluster_radius_mm`, `same_layer_only`, `bridge_openings_mm`, `merge_collinear`,
`include_closed_polylines`.

`bridge_openings_mm` — какой разрыв стены считать проёмом, а не концом стены. Без него стена с
двумя проёмами станет тремя стенами. Значение выбирает человек: две отдельные стены через
коридор выглядят так же, как одна стена с широким проёмом.

## Имена (`naming`)

Текст из чертежа не читается, поэтому имена осей и номера помещений задаются здесь.

```jsonc
"naming": {
  "strategy": "ordered",        // ordered | by_semantic_id | by_position
  "axis": "x",
  "direction": "ascending",
  "values": ["1", "2", "3", "4"],
  "order_tolerance_mm": 50,
  "on_unnamed": "refuse"        // refuse | review | leave_unnamed
}
```

Стратегии по умолчанию нет; `ordered` без `axis` отвергается. Имя, уже занятое в модели,
отвергается до начала построения.

## Параметры (`parameters`)

```jsonc
"parameters": {
  "Комментарии": "из слоя АР_Стены_наружные",
  "Огнестойкость": { "value": "REI 150", "scope": "instance", "required": true }
}
```

`scope: "type"` меняет все экземпляры типа в модели, включая построенные раньше. Опечатка в
имени параметра обнаруживается при применении, а не на репетиции.

## Примеры из документации

Стена:

```jsonc
{
  "id": "external-walls",
  "precedence": 20,
  "discipline": "architecture",
  "layers":         ["A-WALL-EXT*"],
  "exclude_layers": ["A-WALL-EXT-PATT"],
  "produces": "wall",
  "category": "OST_Walls",
  "family_type": "Basic Wall: Exterior - Brick on Block",
  "level": "Level 1",
  "height_mm": 3000,
  "structural": true,
  "min_confidence": 0.6,
  "geometry": {
    "from": "double_lines",
    "min_thickness_mm": 100,
    "max_thickness_mm": 400,
    "min_overlap_fraction": 0.6,
    "bridge_openings_mm": 1500
  }
}
```

Несущая колонна (верхний уровень обязателен для семейств «между двумя уровнями»):

```jsonc
{ "id": "columns", "layers": ["S-COLS-*"], "produces": "structural_column",
  "family_type": "M_Concrete-Round-Column: 300mm",
  "level": "Level 2", "top_level": "Level 3",
  "geometry": { "from": "closed_loops" } }
```

## Заготовки по разделам (не проверены в Revit)

Имена слоёв, типов и уровней здесь условные: замени их на свои. Каждую заготовку прогоняй
через `horizun_plan_from_cad` и правь по его отказам.

### Оси

```jsonc
{ "id": "grids-digits", "discipline": "architecture",
  "layers": ["Оси_цифровые"], "produces": "grid",
  "geometry": { "from": "single_lines", "min_length_mm": 3000 },
  "naming": { "strategy": "ordered", "axis": "x", "direction": "ascending",
              "values": ["1", "2", "3"], "order_tolerance_mm": 50, "on_unnamed": "refuse" } }
```

### КЖ

```jsonc
{ "id": "rc-walls", "discipline": "structure",
  "layers": ["КЖ_Стены"], "produces": "wall",
  "family_type": "Базовая стена: Монолит 200", "level": "Этаж 1", "height_mm": 3300,
  "structural": true,
  "geometry": { "from": "double_lines", "min_thickness_mm": 160, "max_thickness_mm": 400,
                "bridge_openings_mm": 1500 } },

{ "id": "rc-slab", "discipline": "structure",
  "layers": ["КЖ_Плита_контур"], "produces": "floor",
  "family_type": "Перекрытие: Монолит 200", "level": "Этаж 2",
  "structural": true,
  "geometry": { "from": "closed_loops", "min_area_mm2": 1000000 } },

{ "id": "rc-footings", "discipline": "structure",
  "layers": ["КЖ_Фундаменты"], "produces": "foundation",
  "family_type": "Фундамент столбчатый: 1500x1500x600", "level": "Низ фундаментов",
  "geometry": { "from": "closed_loops" } }
```

### АР

```jsonc
{ "id": "partitions", "discipline": "architecture", "precedence": 10,
  "layers": ["АР_Перегородки"], "produces": "wall",
  "family_type": "Базовая стена: Перегородка 120", "level": "Этаж 1", "height_mm": 3000,
  "geometry": { "from": "double_lines", "min_thickness_mm": 80, "max_thickness_mm": 160,
                "bridge_openings_mm": 1200 } },

// второй проход, когда стены уже построены
{ "id": "doors", "discipline": "architecture",
  "layers": ["АР_Двери"], "produces": "door",
  "family_type": "Дверь одностворчатая: 900x2100", "level": "Этаж 1",
  "host_layers": ["АР_Перегородки", "АР_Стены*"],
  "geometry": { "from": "point_clusters", "cluster_radius_mm": 600 } }
```

### КМ

```jsonc
{ "id": "steel-columns", "discipline": "structure",
  "layers": ["КМ_Колонны"], "produces": "structural_column",
  "family_type": "Колонна двутавровая: 30К1", "level": "Этаж 1", "top_level": "Этаж 2",
  "geometry": { "from": "point_clusters", "cluster_radius_mm": 300 } },

{ "id": "steel-beams", "discipline": "structure",
  "layers": ["КМ_Балки"], "produces": "beam",
  "family_type": "Балка двутавровая: 35Б1", "level": "Этаж 2", "offset_mm": 0,
  "geometry": { "from": "single_lines", "min_length_mm": 500 } }
```

### ОВ

```jsonc
{ "id": "supply-ducts", "discipline": "mechanical",
  "layers": ["ОВ_П1_воздуховоды"], "produces": "duct",
  "family_type": "Воздуховод круглого сечения: С отводами",
  "system_type": "Приточный воздух", "diameter_mm": 250,
  "level": "Этаж 1", "offset_mm": 2700,
  "geometry": { "from": "single_lines", "min_length_mm": 200, "merge_collinear": true } },

{ "id": "heating-pipes", "discipline": "mechanical",
  "layers": ["ОВ_Т1"], "produces": "pipe",
  "family_type": "Трубы: Стальные", "system_type": "Подача отопления", "diameter_mm": 25,
  "level": "Этаж 1", "offset_mm": 300,
  "geometry": { "from": "single_lines", "min_length_mm": 200 } }
```

`discipline` — свободный текст, свой словарь. `diameter_mm` задаёт одно сечение на правило. Если на
одном слое лежат воздуховоды разных сечений, либо разнеси их по слоям в AutoCAD, либо проверь по
схемам `horizun_plan_from_cad` и `horizun_cad_networks`, может ли сервер взять размеры и системы
из самого файла DWG (в документации это упомянуто, порядок там не описан подробно).
