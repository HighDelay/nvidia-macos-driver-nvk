# SPDX-License-Identifier: MIT
"""Describe the public desktop GL contract from Mesa's pinned Khronos registry."""
import xml.etree.ElementTree as ET


def requirements(path):
    root = ET.parse(path).getroot()
    versions, extensions = {}, {}
    for profile in ('core', 'compatibility'):
        commands, available = set(), []
        for feature in root.findall('feature'):
            if feature.get('api') != 'gl':
                continue
            for block in feature:
                if block.get('profile') not in (None, profile):
                    continue
                names = {item.get('name') for item in block.findall('command')}
                if block.tag == 'require':
                    commands.update(names)
                elif block.tag == 'remove':
                    commands.difference_update(names)
            available.append({'version': feature.get('number'), 'commands': sorted(commands)})
        versions[profile] = available
        exposed = {}
        for extension in root.findall('extensions/extension'):
            supported = extension.get('supported', '').split('|')
            if not ({'gl', 'glcore'} & set(supported)) or (profile == 'core' and 'glcore' not in supported):
                continue
            names = set()
            for block in extension.findall('require'):
                if block.get('api') not in (None, 'gl', 'glcore') or block.get('profile') not in (None, profile):
                    continue
                names.update(item.get('name') for item in block.findall('command'))
            exposed[extension.get('name')] = sorted(names)
        extensions[profile] = exposed
    return {'versions': versions, 'extensions': extensions}


def supported(contract, commands, profile):
    commands = set(commands)
    version = 0
    for feature in contract['versions'][profile]:
        if set(feature['commands']) <= commands:
            major, minor = map(int, feature['version'].split('.'))
            version = max(version, major * 10 + minor)
    extensions = sorted(name for name, required in contract['extensions'][profile].items()
                        if set(required) <= commands)
    return version, extensions
