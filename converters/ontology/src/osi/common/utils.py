import logging
import re
from keyword import iskeyword


def camel_to_snake(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

def to_pascal_case(text: str) -> str:
    words = re.split(r'[\s_\-\(\)<>:]+', text)
    return ''.join(capitalize_first(word) for word in words)\
           .replace('[', '')\
           .replace(']', '_')\
           .replace('&', 'And')

def capitalize_first(s):
    return s[0].upper() + s[1:] if s else s

digit_names = {'0': 'Zero', '1': 'One', '2': 'Two', '3': 'Three', '4': 'Four',
               '5': 'Five', '6': 'Six', '7': 'Seven', '8': 'Eight', '9': 'Nine'}

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

    # replace leading digits with alpha
    if new_name[0].isdigit():
        new_name = digit_names[new_name[0]] + new_name[1:]

    if new_name != canonical_name:
        logging.warning(f"Verbalization string {verb_string} has unsupported symbols. Replacing them with '_'")
    if iskeyword(new_name):
        new_name = f"{new_name}_k"
        logging.warning(f"Verbalization string {verb_string} is a reserved keyword. Appending '_k' suffix.")
    return new_name