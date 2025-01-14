#!/usr/bin/env python

# Copyright 2021 The Flux authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This script generates OpenAPI v3 JSON schema from Kubernetes CRD YAML
# Derived from https://github.com/yannh/kubeconform
# Derived from https://github.com/instrumenta/openapi2jsonschema

import yaml
import json
import sys
import os
import urllib.request

def iteritems(d):
    if hasattr(dict, "iteritems"):
        return d.iteritems()
    else:
        return iter(d.items())


def additional_properties(data):
    "This recreates the behaviour of kubectl at https://github.com/kubernetes/kubernetes/blob/225b9119d6a8f03fcbe3cc3d590c261965d928d0/pkg/kubectl/validation/schema.go#L312"
    new = {}
    try:
        for k, v in iteritems(data):
            new_v = v
            if isinstance(v, dict):
                if "properties" in v:
                    if "additionalProperties" not in v:
                        v["additionalProperties"] = False
                new_v = additional_properties(v)
            else:
                new_v = v
            new[k] = new_v
        return new
    except AttributeError:
        return data


def replace_int_or_string(data):
    new = {}
    try:
        for k, v in iteritems(data):
            new_v = v
            if isinstance(v, dict):
                if "format" in v and v["format"] == "int-or-string":
                    new_v = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
                else:
                    new_v = replace_int_or_string(v)
            elif isinstance(v, list):
                new_v = list()
                for x in v:
                    new_v.append(replace_int_or_string(x))
            else:
                new_v = v
            new[k] = new_v
        return new
    except AttributeError:
        return data


def allow_null_optional_fields(data, parent=None, grand_parent=None, key=None):
    new = {}
    try:
        for k, v in iteritems(data):
            new_v = v
            if isinstance(v, dict):
                new_v = allow_null_optional_fields(v, data, parent, k)
            elif isinstance(v, list):
                new_v = list()
                for x in v:
                    new_v.append(allow_null_optional_fields(x, v, parent, k))
            elif isinstance(v, str):
                is_non_null_type = k == "type" and v != "null"
                has_required_fields = grand_parent and "required" in grand_parent
                if is_non_null_type and not has_required_field:
                    new_v = [v, "null"]
            new[k] = new_v
        return new
    except AttributeError:
        return data


def append_no_duplicates(obj, key, value):
    """
    Given a dictionary, lookup the given key, if it doesn't exist create a new array.
    Then check if the given value already exists in the array, if it doesn't add it.
    """
    if key not in obj:
        obj[key] = []
    if value not in obj[key]:
        obj[key].append(value)


def insert_api_version_kind_and_objectmeta(schema, api_version, kind, object_meta):
    schema["properties"]["apiVersion"]["enum"] = [api_version]
    schema["properties"]["apiVersion"]["default"] = api_version
    schema["properties"]["kind"]["enum"] = [kind]
    schema["properties"]["kind"]["default"] = kind
    schema["properties"]["metadata"] = object_meta
    return schema


def write_schema_file(schema, api_version, kind, object_meta, filename):
    schemaJSON = ""

    schema = insert_api_version_kind_and_objectmeta(schema, api_version, kind, object_meta)
    schema = additional_properties(schema)
    schema = replace_int_or_string(schema)
    schemaJSON = json.dumps(schema, indent=2)

    # Dealing with user input here..
    filename = os.path.basename(filename)
    f = open(filename, "w")
    f.write(schemaJSON)
    f.close()
    print("{filename}".format(filename=filename))
    return schema


if len(sys.argv) == 0:
    print("missing file")
    exit(1)


# This is the object meta v1 schema file taken from Instrumenta
# https://raw.githubusercontent.com/instrumenta/kubernetes-json-schema/master/master-standalone/objectmeta-meta-v1.json
object_meta_f = open("/objectmeta-meta-v1.json")
object_meta = json.loads(object_meta_f.read())
object_meta_f.close()

one_of = list()

# first arg is the combined schemas filename
combined_schemas_filename = sys.argv[1]

# second arg and the rest are CRD files to process
for crdFile in sys.argv[2:]:
    if crdFile.startswith("http"):
      f = urllib.request.urlopen(crdFile)
    else:
      f = open(crdFile)
    with f:
        for y in yaml.load_all(f, Loader=yaml.SafeLoader):
            if "kind" not in y:
                continue
            if y["kind"] != "CustomResourceDefinition":
                continue

            filename_format = os.getenv("FILENAME_FORMAT", "{kind}-{group}-{version}")
            filename = ""
            if "spec" in y and "validation" in y["spec"] and "openAPIV3Schema" in y["spec"]["validation"]:
                kind = y["spec"]["names"]["kind"]
                version = y["spec"]["version"]
                filename = filename_format.format(
                    kind=kind,
                    group=y["spec"]["group"].split(".")[0],
                    version=version,
                ).lower() + ".json"

                api_version = y["spec"]["group"] + "/" + version
                schema = y["spec"]["validation"]["openAPIV3Schema"]
                schema = write_schema_file(schema, api_version, kind, object_meta, filename)
                one_of.append(schema)
            elif "spec" in y and "versions" in y["spec"]:
                for version in y["spec"]["versions"]:
                    if "schema" in version and "openAPIV3Schema" in version["schema"]:
                        kind = y["spec"]["names"]["kind"]
                        filename = filename_format.format(
                            kind=kind,
                            group=y["spec"]["group"].split(".")[0],
                            version=version["name"],
                        ).lower() + ".json"

                        api_version = y["spec"]["group"] + "/" + version["name"]
                        schema = version["schema"]["openAPIV3Schema"]
                        schema = write_schema_file(schema, api_version, kind, object_meta, filename)
                        one_of.append(schema)


all_schemas = {
    "description": "Auto-generated CRD JSON schema for Flux",
    "title": "Flux CRD JSON schemas",
    "$schema": "http://json-schema.org/draft-04/schema#",
    "oneOf": one_of,
}

all_schemas_json = json.dumps(all_schemas, indent=2)
all_schemas_f = open(combined_schemas_filename, "w")
all_schemas_f.write(all_schemas_json)
all_schemas_f.close()
print(combined_schemas_filename)

exit(0)
