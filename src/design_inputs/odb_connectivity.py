"""ODB EDA FID associations: source indices, never proximity-inferred nets."""
import shlex


def feature_links(text):
    layers, links, warnings = [], {}, []
    net, subnet, connection = "", {}, 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line.split(";", 1)[0])
            if tokens[0] == "LYR":
                layers = tokens[1:]
            elif tokens[0] == "NET":
                net, subnet = "", {}
                net = tokens[1] if tokens[1] != "$NONE$" else ""
            elif tokens[0] == "SNT":
                connection += 1
                subnet = dict(kind=tokens[1], connection_id=connection)
                if tokens[1] == "TOP":
                    subnet.update(side="TOP" if tokens[2] == "T" else "BOTTOM" if tokens[2] == "B" else "",
                                  component_index=int(tokens[3]), pin_index=int(tokens[4]))
            elif tokens[0] == "FID":
                layer, feature = int(tokens[2]), int(tokens[3])
                if not 0 <= layer < len(layers) or feature < 0:
                    raise ValueError("FID 层/图元索引超出范围")
                key = (layers[layer].casefold(), feature)
                record = dict(subnet, net=net, fid_type=tokens[1])
                if key in links and links[key] != record:
                    warnings.append(f"行{lineno}: FID 关联冲突 {key}")
                    links[key] = dict(kind="conflict", net="")
                else:
                    links[key] = record
        except (ValueError, IndexError) as exc:
            if line.startswith("SNT"):
                subnet = dict(kind="invalid")
            warnings.append(f"行{lineno}: EDA {exc}")
    return links, warnings
