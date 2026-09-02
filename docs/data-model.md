# Observation model and exported representations

[Documentation index](README.md)

## Provenance and time

The live packet begins with a selected PyVantagePro dictionary. The publisher does not independently decode raw Davis packets. For most console fields, it preserves the dependency's values in CSV and MQTT. Exact units, missing-value conventions, and available channels must therefore be established against the deployed dependency revision and console configuration.

The main loop adds the following metadata:

| Field | Origin and interpretation |
| --- | --- |
| `Datetime` | Host UTC time assigned during main-loop processing, formatted `YYYY-MM-DDTHH:MM:SSZ` |
| `DatetimeWS` | Original console `Datetime`, only when that field survived filtering |
| `position` | Object containing configured `latitude` and `longitude` |
| `name` | Configured station label |

`Datetime` is not a guaranteed instrument measurement time or delivery time. It is assigned before optional AirLink retrieval and transport operations. `DatetimeWS` preserves the original value without timezone normalization; Python datetime values are serialized with `isoformat()` in JSON. Analyses should not silently equate the two timestamps.

No explicit sensor quality flags, calibration history, observation identifier, or schema version are added. If these are needed for a scientific data product, preserve them in an associated provenance record. A downstream duplicate key based on station identity and `Datetime` alone may collide because generated timestamps have only whole-second precision.

## Flat MQTT representation

The `flat` representation is the enriched packet itself. It does not automatically add a `uuid` property; station identity is carried by the MQTT topic. A representative packet is:

```json
{
  "Datetime": "2026-09-03T10:15:40Z",
  "DatetimeWS": "2026-09-03T12:15:39",
  "TempOut": 20.0,
  "HumOut": 55.0,
  "Barometer": 1013.25,
  "position": {"latitude": 40.8569, "longitude": 14.2845},
  "name": "Example station"
}
```

The console timestamp above is illustrative; its timezone cannot be inferred from the string alone. JSON packets use compact separators on the wire. Python datetime objects are supported, but arbitrary dependency-specific object types are not. The encoder is not configured to reject non-finite floating-point values, so strict JSON consumers need a deployment check for such values.

## GeoJSON representation

The `geojson` format wraps the same enriched packet as a Point feature. The geometry uses longitude before latitude. Properties include the enriched packet plus `uuid` and `name`; consequently the existing `position` property is retained as well as the geometry.

```json
{
  "type": "Feature",
  "geometry": {"type": "Point", "coordinates": [14.2845, 40.8569]},
  "properties": {
    "Datetime": "2026-09-03T10:15:40Z",
    "TempOut": 20.0,
    "position": {"latitude": 40.8569, "longitude": 14.2845},
    "uuid": "it.example.meteo.ws1",
    "name": "Example station"
  }
}
```

Selecting this representation changes only the MQTT JSON envelope. It does not alter the CSV row, Signal K paths, MQTT topic, or console selection rules.

## CSV organization and schema evolution

The live storage path is:

```text
<pathStorage>/<uuid>/<YYYY>/<MM>/<DD>/<uuid>_<YYYYMMDD>Z<HH>00.csv
```

In ordinary live operation these date components come from the generated UTC `Datetime`. The storage helper parses a supplied datetime but does not normalize arbitrary offset timestamps to UTC before choosing a filename; callers outside the normal loop must account for that behavior. If parsing fails, the helper falls back to the host's current UTC time.

Each file starts with a header. When a new observation introduces fields, the helper appends their names to the existing header, rewrites prior rows into a temporary file, and atomically replaces the original file before appending the new row. Older rows acquire empty cells for newly introduced columns. If the rewrite fails, the original file is preserved and the new row is skipped with an error log. Normal appends are not transactional with MQTT or Signal K.

CSV values follow Python's `csv.DictWriter` serialization rather than a separate tabular schema. `None` becomes an empty cell; a nested `position` dictionary becomes its Python string representation, not an embedded JSON document. Datetime formatting can consequently differ between CSV and JSON. Consumers should read by column name and should not assume that different hourly files have identical headers or column counts. No automatic CSV retention policy is implemented.

## Signal K conversion contract

A delta contains the configured context and one update with a timestamp and a `values` list. Each update includes `navigation.position`. Metadata keys `position`, `name`, `uuid`, `Datetime`, and `DatetimeWS` are otherwise excluded from values. This structure follows the general delta concept described in the [Signal K data model](https://signalk.org/specification/1.7.0/doc/data_model.html); application-specific paths and contexts still require server validation.

The current conversion function applies the following formulas by original field name:

| Source key | Assumed incoming unit | Output transformation | Built-in destination path |
| --- | --- | --- | --- |
| `TempOut` | °C | `x + 273.15` → K | `environment.outside.temperature` |
| `TempIn` | °C | `x + 273.15` → K | `environment.inside.temperature` |
| `HumOut` | Percent | `x / 100` → fraction | `environment.outside.humidity` |
| `HumIn` | Percent | `x / 100` → fraction | `environment.inside.humidity` |
| `Barometer` | hPa | `x × 100` → Pa | `environment.outside.pressure` |
| `WindDir` | Degrees | `x × π / 180` → radians | `environment.wind.angleApparent` |
| `WindSpeed` | Dependency-provided | Unchanged | `environment.wind.speedApparent` |

These are implementation assumptions, not a verified statement about every PyVantagePro revision. For example, changing a path to a different quantity does not change its conversion formula. A custom map from `TempOut` still receives the temperature conversion, whereas an AirLink key named `temp` receives none.

For other fields, the program first uses `signalkPathMap`; absent an override, it uses the built-in table; absent either, it creates `environment.<sanitized-key>`. Sanitization retains alphanumeric characters and underscores and substitutes other characters with underscores. It does not validate the resulting path against a server schema. The extensive example mapping in the root README is configuration, not an extension of the seven built-in defaults above.

Null values are omitted. If conversion raises an exception, the unconverted raw value is used instead. The program therefore does not guarantee a uniform numeric type or correct physical unit for every mapped value. Verify representative observations against the receiving application before relying on the deltas for quantitative analysis.

## AirLink enrichment

`airlink.py` selects the first sensor whose first data record contains `hum`. It does not explicitly select a sensor type or iterate over all records within a sensor. The following keys can be merged:

| Group | Fields |
| --- | --- |
| Meteorology | `hum`, `temp`, `heat_index`, `dew_point`, `wet_bulb`, `bar` |
| Particulate values | `pm_1`, `pm_2p5`, `pm_10` |
| PM2.5 summaries | `pm_2p5_1_hour`, `pm_2p5_3_hour`, `pm_2p5_24_hour`, `pm_2p5_nowcast` |
| PM10 summaries | `pm_10_1_hour`, `pm_10_3_hour`, `pm_10_24_hour`, `pm_10_nowcast` |
| Air quality indices | `aqi_val`, `aqi_1_hour_val`, `aqi_nowcast_val` |

The four temperature fields are converted with `(F − 32) × 5/9`. `bar` is multiplied by `33.8639`, yielding hPa when the incoming value is inHg. Other selected fields are converted to floats without a unit transformation. Invalid numeric values are skipped individually. Index algorithms, averaging windows, and particulate units are not defined by this publisher and should be checked against the data provider.

Cached AirLink observations can be repeated across multiple console packets. No AirLink measurement timestamp or cache-age field is exported, so enrichment does not imply simultaneous acquisition. A key collision would be resolved in favor of AirLink because the merge uses dictionary update.

Implementation basis: [payload, storage, and conversion functions](../vantage-publisher.py) and [AirLink extraction](../airlink.py).
