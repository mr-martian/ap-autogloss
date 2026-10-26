#!/usr/bin/python3

import collections
import dataclasses
import subprocess
import tomllib

@dataclasses.dataclass
class SlotException:
    stems: set[str] = dataclasses.field(default_factory=set)
    tags: set[tuple] = dataclasses.field(default_factory=set)
    mapping: dict[str, str] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(lambda: ''))

    def applies(self, word):
        if self.stems and word.source_lemma.lower() not in self.stems:
            return False
        if self.tags:
            for tg in self.tags:
                if set(tg) < set(word.source_tags):
                    return True
            return False
        return True

    def from_config(self, blob, mapping):
        self.stems.update(blob.get('stems', []))
        tg = blob.get('tags', [])
        if isinstance(tg, str):
            tg = tg.split('.')
        self.tags.add(tuple(tg))
        self.mapping.update(mapping)

@dataclasses.dataclass
class Slot:
    is_stem: bool = False
    default: dict[str, str] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(lambda: ''))
    exceptions: list[SlotException] = dataclasses.field(
        default_factory=list)

    def get_value(self, word):
        if self.is_stem:
            return word.target_lemma or f'({word.source_lemma})'
        ret = ''
        for tg in word.source_tags:
            ret = ret or self.default[tg]
        for ex in self.exceptions:
            if ex.applies(word):
                for tg in word.source_tags:
                    ret = ex.mapping[tg] or ret
                break
        return ret

    def from_config(self, blob):
        if 'tags' in blob:
            self.default.update(blob['tags'])
        if 'exceptions' in blob:
            for blob2 in blob['exceptions']:
                dct = {k: v for k, v in blob2.items()
                       if k not in ['stems', 'tags', 'contexts']}
                if 'stems' in blob2 or 'tags' in blob2:
                    ex = SlotException()
                    ex.from_config(blob2, dct)
                    self.exceptions.append(ex)
                for ctx in blob2.get('contexts', []):
                    ex = SlotException()
                    ex.from_config(ctx, dct)
                    self.exceptions.append(ex)

@dataclasses.dataclass
class Config:
    pos: dict[tuple, list[str]] = dataclasses.field(default_factory=dict)
    slots: dict[tuple[str, tuple], Slot] = dataclasses.field(
        default_factory=dict)
    seg_mode: str = ''
    seg_mode_directory: str = ''

    def add_config(self, name: tuple, value):
        if not isinstance(value, dict):
            return
        if 'slots' in value:
            self.pos[name] = value['slots']
        if 'tags' in value or 'exceptions' in value:
            s = Slot()
            s.from_config(value)
            self.slots[(name[-1], name[:-1])] = s
        for k, v in value.items():
            if k in ['slots', 'tags', 'exceptions']:
                continue
            self.add_config(name+(k,), v)

    def find_pattern(self, word):
        key = tuple(word.source_tags)
        while key not in self.pos:
            key = key[:-1]
        return self.pos[key]

    def get_slot(self, word, slot):
        key = tuple(word.source_tags)
        while (slot, key) not in self.slots:
            if not key:
                if word.source_tags:
                    raise ValueError(f'Slot {slot} is not defined for POS tag <{word.source_tags[0]}>!')
                else:
                    raise ValueError(f'Slot {slot} is not defined! (Attempted to apply to word with no POS tag)')
            key = key[:-1]
        return self.slots[(slot, key)]

def load_config(fname, seg_mode, seg_mode_directory):
    with open(fname, 'rb') as fin:
        blob = tomllib.load(fin)
    ret = Config(seg_mode=seg_mode, seg_mode_directory=seg_mode_directory)
    ret.slots[('stem', ())] = Slot(is_stem=True)
    ret.pos[tuple()] = ['stem']
    ret.add_config(tuple(), blob)
    return ret

@dataclasses.dataclass
class Word:
    surface: str = ''
    source_lemma: str = ''
    source_tags: list[str] = dataclasses.field(default_factory=list)
    source_string: str = ''
    target_lemma: str = ''
    morphemes: list[str] = dataclasses.field(default_factory=list)
    segments: list[str] = dataclasses.field(default_factory=list)

    def label_segments(self, config):
        pat = config.find_pattern(self)
        for slot_name in pat:
            slot = config.get_slot(self, slot_name)
            self.segments.append(slot.get_value(self))

def parse_stream(line):
    state = ['init']
    cur = ''
    cur_tag = ''
    cur_word = None
    for c in line:
        match (c, state[-1]):
            case (_, 'esc'):
                cur += c
                state.pop()
            case ('\\', _):
                cur += c
                state.append('esc')
            case ('[', 'init'):
                state.append('blank')
            case (']', 'blank'):
                state.pop()
            case (_, 'blank'):
                pass
            case ('^', 'init'):
                state.append('surf')
                cur_word = Word()
                cur = ''
                cur_tag = ''
            case ('/', 'surf'):
                cur_word.surface = cur
                cur = ''
                state[-1] = 'src'
            case ('/', 'src'):
                cur_word.source_string = cur
                cur = ''
                state[-1] = 'tgt'
            case ('/', 'tgt'):
                state[-1] = 'end'
                cur = ''
            case ('<', 'src') | ('<', 'tgt'):
                if state[-1] == 'src' and not cur_word.source_lemma:
                    cur_word.source_lemma = cur
                elif state[-1] == 'tgt' and not cur_word.target_lemma:
                    cur_word.target_lemma = cur
                state.append('tag')
                cur += c
                cur_tag = ''
            case ('>', 'tag'):
                cur += c
                state.pop()
                if state[-1] == 'src':
                    cur_word.source_tags.append(cur_tag)
                cur_tag = ''
            case ('$', 'tgt') | ('$', 'end'):
                if cur and not cur_word.target_lemma:
                    cur_word.target_lemma = 'cur'
                yield cur_word
                cur_word = None
                state.pop()
            case ('$', 'surf') | ('$', 'src'):
                # bad LU, skip
                cur_word = None
                state.pop()
            case _:
                cur += c
                cur_tag += c

def process_stream(fin, config):
    pipe = None
    if config.seg_mode:
        cmd = ['apertium']
        if config.set_mode_directory:
            cmd += ['-d', config.seg_mode_directory]
        cmd.append(config.seg_mode)
        pipe = subprocess.run(cmd, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,
                              encoding='utf-8')
    for line in fin:
        cur = []
        for w in parse_stream(line):
            w.label_segments(config)
            if pipe is not None:
                pipe.stdin.write('^'+w.source_string+'$\0')
                pipe.stdin.flush()
                out = pipe.stdout.read().lstrip('^').rstrip('$\0')
                w.morphemes = out.split('>')
            cur.append(w)
        yield cur
    if pipe is not None:
        pipe.stdin.flush()
        pipe.stdin.close()

def print_line_text(line, fout):
    surf = ''
    morph = ''
    gloss = ''
    for w in line:
        surf += w.surface
        morph += '-'.join(w.morphemes)
        gloss += ''.join(w.segments)
        mx = max(len(surf), len(morph), len(gloss)) + 1
        surf += ' '*(mx-len(surf))
        morph += ' '*(mx-len(morph))
        gloss += ' '*(mx-len(gloss))
    if not surf.isspace():
        print(surf.rstrip(), file=fout)
    if not morph.isspace():
        print(morph.rstrip(), file=fout)
    if not gloss.isspace():
        print(gloss.rstrip(), file=fout)
    print('', file=fout)

if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser('Convert Apertium stream to IGT')
    parser.add_argument('config', action='store',
                        help='Configuration file')
    parser.add_argument('-i', '--infile', action='store',
                        help='Read from file (rather than stdin)')
    parser.add_argument('-o', '--outfile', action='store',
                        help='Write to file (rather than stdout)')
    parser.add_argument('-f', '--format', action='store',
                        default='text', choices=['text'],
                        help='Output format')
    parser.add_argument('-s', '--seg-mode', action='store',
                        help='Segmentation mode')
    parser.add_argument('-d', '--seg-mode-directory', action='store',
                        help='Segmentation mode directory')
    args = parser.parse_args()

    config = load_config(args.config, args.seg_mode,
                         args.seg_mode_directory)

    def maybe_open(fname, fdir):
        if not fname:
            if fdir == 'w':
                return sys.stdout
            else:
                return sys.stdin
        else:
            return open(fname, fdir)

    with (maybe_open(args.infile, 'r') as fin,
          maybe_open(args.outfile, 'w') as fout):
        for line in process_stream(fin, config):
            if args.format == 'text':
                print_line_text(line, fout)
