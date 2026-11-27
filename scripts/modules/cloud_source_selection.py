"""Select LAS/LAZ sources by registered entity ownership before spatial cropping.

Ownership can be expressed by an exact path component (separate folder) or
an exact entity token in a LAS/LAZ filename. Entity tokens are matched against
the registered AOI IDs and cannot match substrings (9L6 != 9L66).
"""
from __future__ import annotations

import re
from pathlib import Path


def _token_match(text: str, entity: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(entity) + r"(?![A-Za-z0-9])",
                          text, flags=re.IGNORECASE))


def select_entity_clouds(folder: Path, entity: str, registered_entities):
    """Return (files, selection_mode).

    If *no* sources contain registered entity references, every source is
    common to all AOIs. When tagged files exist, use only those explicitly
    belonging to the target entity, not other circuits or unlabelled files.
    This conservative approach prevents silently merging overlapping survey
    sets. Mixed labelled/unlabelled input should be reorganised explicitly.
    """
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"Point-cloud source folder not found: {root}")
    files = sorted((p for p in root.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".las", ".laz")),
                   key=lambda p: str(p).casefold())
    if not files:
        raise FileNotFoundError(f"No LAS/LAZ files below {root}")
    names = sorted(set(str(x) for x in registered_entities if x),
                   key=len, reverse=True)
    names = sorted(set([*names, str(entity)]), key=len, reverse=True)
    tagged = {}
    untagged = []
    for path in files:
        rel = path.relative_to(root)
        owners = {name for name in names
                  if _token_match(path.stem, name)
                  or any(part.casefold() == name.casefold()
                         for part in rel.parts[:-1])}
        if len(owners) > 1:
            raise ValueError(f"Ambiguous circuit/area attribution in {path}: {sorted(owners)}")
        if owners:
            tagged[path] = next(iter(owners))
        else:
            untagged.append(path)
    if not tagged:
        return files, "shared"
    chosen = [p for p, owner in tagged.items()
              if owner.casefold() == entity.casefold()]
    if not chosen:
        raise ValueError(
            f"No circuit-specific LAS/LAZ source for {entity}. "
            f"Found sources labelled for other entities. "
            f"Use a folder or filenames containing {entity}, "
            "or provide a completely unlabelled shared dataset.")
    return chosen, "entity-specific"
