"""
Standalone tools that are complementary to KIMMDY.
"""

from pathlib import Path
import shutil
import argparse
from typing import Optional
import json

from kimmdy.topology.topology import Topology
from kimmdy.parsing import read_top, write_top
from kimmdy.plugins import parameterization_plugins
from kimmdy.plugins import discover_plugins


def build_examples(restore: str):
    """Build example directories for KIMMDY from integration tests.

    Parameters
    ----------
    restore
        If True, overwrite input files in existing example directories. If "hard", also delete output files.
    """
    print(f'build_examples with restore option "{restore}"')
    overwrite = True if restore else False

    example_directories = [
        "alanine_hat_naive",
        "hexalanine_homolysis",
        "charged_peptide_homolysis_hat_naive",
        "triplehelix_pull",
        "hexalanine_single_reaction",
    ]
    root_dir = Path(__file__).parents[2]
    testfiles_path = root_dir / "tests" / "test_files" / "test_integration"
    examples_path = root_dir / "example"
    assets_path = Path(".") / ".." / ".." / "tests" / "test_files" / "assets"

    for directory in example_directories:
        try:
            print("Building example", directory, "...", end="")
            src = testfiles_path / directory
            dest = examples_path / directory
            if restore == "hard":
                shutil.rmtree(dest, ignore_errors=True)
            print(src, dest)
            shutil.copytree(src, dest, dirs_exist_ok=overwrite)
            (dest / "amber99sb-star-ildnp.ff").unlink(missing_ok=True)
            (dest / "amber99sb-star-ildnp.ff").symlink_to(
                assets_path / "amber99sb-star-ildnp.ff",
                target_is_directory=True,
            )
            print("done building examples")
        except FileExistsError as e:
            raise FileExistsError(
                f"Could not build example directory {directory} because it already exists. Try the --restore option to still build it."
            ) from e


def get_build_example_cmdline_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns
    -------
    Namespace
        parsed command line arguments
    """
    parser = argparse.ArgumentParser(description="Build examples for KIMMDY.")
    parser.add_argument(
        "-r",
        "--restore",
        const=True,
        nargs="?",
        type=str,
        help="Overwrite input files in existing example directories, use keyword 'hard' to also delete output files.",
    )
    return parser.parse_args()


def entry_point_build_examples():
    """Build examples from the command line."""
    args = get_build_example_cmdline_args()
    build_examples(args.restore)


def modify_top(
    topology: str,
    out: str,
    parameterize: bool = False,
    removeH: Optional[list[int]] = None,
    gro: Optional[str] = None,
    residuetypes: Optional[str] = None,
    radicals: Optional[list[int]] = None,
    search_amber_rad: bool = True,
):
    """Modify topology in various ways.

    Parameters
    ----------
    topology
        Path to GROMACS top file
    out
        Output topology file path, stem also used for gro.
        Can be relative to cwd.
    parameterize
        Parameterize topology with grappa after removing hydrogen
    removeH
        Remove one or more hydrogens by atom nrs in the top file.
        One based.
    gro
        GROMACS gro input file. Updates structure when deleting H.
        Output named like top output.
    residuetypes
        GROMACS style residuetypes file. Necessary for parameterization with
        non-amber atom types.
    radicals
        Radicals in the system PRIOR to removing hydrogens with the removeH
        option. One based. Can be detected automatically in amber topologies.
    search_amber_rad
        Automatic radical search only implemented for amber. If you do use
        another ff, set this to false, and provide a list of radicals
        manually, if necessary.
    """

    top_path = Path(topology).with_suffix(".top").resolve()
    assert top_path.exists(), f"Error finding top {top_path}"

    out_path = Path(out).with_suffix(".top")
    if not out_path.is_absolute():
        out_path = out_path.resolve()

    update_map = {}
    gro_path = None
    gro_out = None
    residuetypes_path = Path(residuetypes) if residuetypes else None

    if gro:
        if removeH:
            gro_path = Path(gro).resolve()
            assert gro_path.exists(), f"Error finding gro {gro_path}"

            gro_out = out_path.with_suffix(".gro")
            while gro_out.exists():
                gro_out = gro_out.with_stem(gro_out.stem + "_mod")

    print(
        "Changing topology\n"
        f"top: \t\t\t{top_path}\n"
        f"output: \t\t{out_path}\n"
        f"parameterize: \t\t{parameterize}\n"
        f"remove hydrogen: \t{removeH}\n"
        f"optional gro: \t\t{gro_path}\n"
        f"gro output: \t\t{gro_out}\n"
        f"residuetypes: \t\t{residuetypes_path}\n"
        f"radicals: \t\t{radicals}\n"
    )

    print("Reading topology..", end="")
    top = Topology(
        read_top(top_path),
        radicals=radicals,
        residuetypes_path=residuetypes_path,
        search_amber_rad=search_amber_rad,
    )
    print("Done")

    # remove hydrogen
    if removeH:
        json_out = out_path.with_name("modify_top_dict.json")
        while json_out.exists():
            json_out = json_out.with_stem(json_out.stem + "_mod")

        print("Removing Hydrogens..", end="")
        broken_idxs = []
        # check for input validity
        for i, nr in enumerate(removeH):
            if not (atom_type := top.atoms[str(nr)].type).upper().startswith("H"):
                print(
                    f"Wrong atom type {atom_type} with nr {nr} for remove hydrogen, should start with 'H'."
                )
                broken_idxs.append(i)
                continue
        for broken_idx in sorted(broken_idxs, reverse=True):
            removeH.pop(broken_idx)

        update_map = top.del_atom(
            [str(nr) for nr in removeH], parameterize=parameterize
        )
        with open(json_out, "w") as f:
            json.dump(update_map, f, indent=0)

        print("Done")

    # parameterize with grappa
    if parameterize:
        # load grappa
        print("Loading Plugins..", end="")
        discover_plugins()
        if "grappa" in parameterization_plugins.keys():
            top.parametrizer = parameterization_plugins["grappa"]()
        else:
            raise KeyError(
                "No grappa in parameterization plugins. Can't continue to parameterize molecule"
            )
        # require parameterization when writing topology to dict
        top.needs_parameterization = True
        print("Done")
        print("Parameterizing and ", end="")

    # write top file
    print("Writing top..", end="")
    write_top(top.to_dict(), out_path)
    print("Done")

    # deal with gro file
    if gro:
        if removeH:
            print("Writing gro..", end="")
            with open(gro_path, "r") as f:
                gro_raw = f.readlines()

            with open(gro_out, "w") as f:
                f.write(gro_raw[0])
                f.write(f"   {str(int(gro_raw[1])-len(removeH))}\n")

                for i, line in enumerate(gro_raw[2:]):
                    if i + 1 in removeH:
                        continue
                    f.write(line)
            print("Done")
        else:
            print(
                "Gro file supplied but no action requested that requires changes to it."
            )
    return update_map


def get_modify_top_cmdline_args() -> argparse.Namespace:
    """
    parse cmdline args for modify_top
    """
    parser = argparse.ArgumentParser(
        description="Welcome to the KIMMDY modify top module",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("top", help="GROMACS top file")
    parser.add_argument(
        "out", help="Output top file name. Stem reused for gro if applicabel."
    )

    parser.add_argument(
        "-p",
        "--parameterize",
        action="store_true",
        help="Parameterize topology with grappa.",
        default=False,
    )
    parser.add_argument(
        "-r",
        "--removeH",
        help="Remove one or more hydrogens by atom nrs in the top file.",
        nargs="+",
        type=int,
    )
    parser.add_argument(
        "-c",
        "--gro",
        help="If necessary, also apply actions on gro file to create a "
        "compatible gro/top file pair. Output analog to top.",
        type=str,
    )
    parser.add_argument(
        "-a",
        "--search_amber_rad",
        action="store_true",
        help="Automatic radical search only implemented for amber. If you do"
        "use another ff, set this to false, and provide a list of radicals"
        "manually, if necessary.",
        default=True,
    )
    parser.add_argument(
        "-t",
        "--residuetypes",
        help="GROMACS style residuetypes file. Necessary for parameterization with non-amber atom types.",
        type=str,
    )
    parser.add_argument(
        "-x",
        "--radicals",
        help="Radicals in the system PRIOR to removing hydrogens with the removeH option.",
        nargs="+",
        type=int,
    )
    return parser.parse_args()


def entry_point_modify_top():
    """Modify topology file in various ways"""
    args = get_modify_top_cmdline_args()

    modify_top(
        topology=args.top,
        out=args.out,
        parameterize=args.parameterize,
        removeH=args.removeH,
        gro=args.gro,
        residuetypes=args.residuetypes,
        radicals=args.radicals,
        search_amber_rad=args.search_amber_rad,
    )


# dot graphs
def topology_to_edgelist(top: Topology):
    """Convert a topology to a list of edges for a dot graph."""
    ls = []
    for b in top.bonds:
        ai = b[0]
        aj = b[1]
        x = top.atoms[ai]
        y = top.atoms[aj]
        left = f'"{ai} {x.type}" '
        right = f'"{aj} {y.type}" '
        ls.append(f"{left} -- {right};")
        if x.is_radical:
            ls.append(f'{left} [color="red"]')
        if y.is_radical:
            ls.append(f'{right} [color="red"]')
    return ls


def edgelist_to_dot_graph(ls: list[str], overlap: str = "true"):
    """Convert a list of edges to a dot graph."""
    header = f"""
        graph G {{
          layout=neato
          overlap={overlap}
          node [shape="circle"]
    """
    tail = """
        }
    """
    body = "\n".join(ls)
    return header + body + tail


def top_to_graph(top: Topology, overlap: str = "true"):
    """Convert a topology to a dot graph.

    Can be used in notebooks to write a dot file and render with quarto.
    """
    return edgelist_to_dot_graph(topology_to_edgelist(top), overlap)


def write_top_as_dot(top: Topology, path: str, overlap: str = "true"):
    """Write a topology as a dot graph to a file.

    Can be used in notebooks to write a dot file and render with quarto.
    """
    with open(path, "w") as f:
        f.write(top_to_graph(top, overlap))
