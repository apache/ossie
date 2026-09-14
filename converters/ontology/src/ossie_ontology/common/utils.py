# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import logging
import re
from keyword import iskeyword


def camel_to_snake(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

digit_names = {'0': 'Zero', '1': 'One', '2': 'Two', '3': 'Three', '4': 'Four',
               '5': 'Five', '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine'}

def to_pascal_case(text: str) -> str:
    name = ''.join(capitalize_first(word) for word in re.split(r'[\s_\-\(\)<>:]+', text))\
           .replace('[', '')\
           .replace(']', '_')\
           .replace('&', 'And')
    # A leading digit is no more legal in a concept name than in a verbalization
    # string, and this one reaches the formula lexer: spell it out, keeping the
    # rest pascal-cased ('3m corp' -> 'ThreeMCorp').
    if name and name[0].isdigit():
        name = digit_names[name[0]] + capitalize_first(name[1:])
    return name

def capitalize_first(s):
    return s[0].upper() + s[1:] if s else s

def to_verbalization_string(verb_string: str) -> str:
    canonical_name = verb_string.lower().strip()
    # replace ' ' and '-' with '_'
    canonical_name = re.sub(r'[-\s]', '_', canonical_name)
    # drop subsequent '_'
    canonical_name = re.sub(r'_+', '_', canonical_name)
    # replace unsupported symbols with '_'
    new_name = re.sub(r'[^a-zA-Z0-9_-]', '_', canonical_name)

    if not new_name:
        raise ValueError(f"Verbalization string {verb_string!r} reduces to an empty identifier after normalisation")

    if new_name != canonical_name:
        logging.warning(f"Verbalization string {verb_string} has unsupported symbols. Replacing them with '_'")

    # replace leading digits with alpha; after the comparison above, which is
    # only about the symbol substitution
    if new_name[0].isdigit():
        new_name = digit_names[new_name[0]] + new_name[1:]

    if iskeyword(new_name):
        new_name = f"{new_name}_k"
        logging.warning(f"Verbalization string {verb_string} is a reserved keyword. Appending '_k' suffix.")
    return new_name
