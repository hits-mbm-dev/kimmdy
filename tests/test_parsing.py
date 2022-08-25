#%%
#%autoreload
import os
import string
from hypothesis import given, strategies as st
from kimmdy import parsing
from pathlib import Path
from copy import deepcopy


def set_dir():
    try:
        test_dir = Path(__file__).parent / "test_files/test_parsing"
    except NameError:
        test_dir = Path("./tests/test_files/test_parsing")
    os.chdir(test_dir)


set_dir()

#%%
#### Example file urea.gro ####
# from <https://manual.gromacs.org/documentation/current/reference-manual/topologies/topology-file-formats.html>
# should parse
def test_parser_doesnt_crash_on_example():
    set_dir()
    urea_path = Path("urea.gro")
    top = parsing.read_topol(urea_path)
    assert isinstance(top, dict)


#%%
#### Parsing it's own output should return the same top on urea.gro ####
def test_doubleparse_urea():
    set_dir()
    urea_path = Path("urea.gro")
    top = parsing.read_topol(urea_path)
    p = Path("pytest_urea.top")
    parsing.write_topol(top, p)
    top2 = parsing.read_topol(p)
    p2 = Path("pytest_urea2.top")
    parsing.write_topol(top2, p2)
    top3 = parsing.read_topol(p2)
    assert top2 == top3


def test_split_dihedrals():
    set_dir()
    top_path = Path("pytest_urea.top")
    top = parsing.read_topol(top_path)
    top_tmp = parsing.topol_split_dihedrals(deepcopy(top))
    top_compare = parsing.topol_merge_propers_impropers(deepcopy(top_tmp))
    assert top == top_compare


#%%
#### Parsing should be invertible ####
allowed_text = st.text(
    string.ascii_letters + string.digits + "!\"$%&'()*+,-./:<=>?@\\^_`{|}~", min_size=1
)


@given(
    d=st.dictionaries(
        allowed_text,
        st.lists(st.lists(allowed_text, min_size=1), min_size=1),
        min_size=1,
    )
)
def test_parser_invertible(d):
    p = Path("tmp/pytest_topol.top")
    p.parent.mkdir(exist_ok=True)
    parsing.write_topol(d, p)
    d2 = parsing.read_topol(p)
    assert d == d2
