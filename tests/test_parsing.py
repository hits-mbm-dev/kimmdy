# %%
import os
import re
import string
from hypothesis import settings, HealthCheck, given, strategies as st
from kimmdy import parsing
from pathlib import Path
import pytest
import shutil

def setup_testdir(tmp_path) -> Path:
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    try:
        filedir = Path(__file__).parent / "test_files" / "test_parsing"
        assetsdir = Path(__file__).parent / "test_files" / "assets"
    except NameError:
        filedir = Path("./tests/test_files") / "test_parsing"
        assetsdir = Path("./tests/test_files") / "assets"
    shutil.copytree(filedir, tmp_path)
    shutil.copy2(assetsdir / "amber99sb_patches.xml", tmp_path)
    Path(tmp_path / "amber99sb-star-ildnp.ff").symlink_to(
        assetsdir / "amber99sb-star-ildnp.ff",
        target_is_directory=True,
    )
    os.chdir(tmp_path.resolve())
    return tmp_path

def set_dir():
    try:
        test_dir = Path(__file__).parent / "test_files/test_parsing"
    except NameError:
        test_dir = Path("./tests/test_files/test_parsing")
    os.chdir(test_dir)


def test_parser_doesnt_crash_on_example(tmp_path, caplog):
    """Example file urea.gro
    from <https://manual.gromacs.org/documentation/current/reference-manual/topologies/topology-file-formats.html>
    """
    testdir = setup_testdir(tmp_path)
    urea_path = Path("urea.gro")
    top = parsing.read_top(urea_path)
    assert isinstance(top, dict)


# %%
def test_doubleparse_urea(tmp_path):
    """Parsing it's own output should return the same top on urea.gro"""
    testdir = setup_testdir(tmp_path)
    urea_path = Path("urea.gro")
    top = parsing.read_top(urea_path)
    p = Path("pytest_urea.top")
    parsing.write_top(top, p)
    top2 = parsing.read_top(p)
    p2 = Path("pytest_urea2.top")
    parsing.write_top(top2, p2)
    top3 = parsing.read_top(p2)
    assert top2 == top3


#### Parsing should be invertible ####
allowed_text = st.text(
    string.ascii_letters + string.digits + "!\"$%&'()*+,-./:<=>?@\\^_`{|}~", min_size=1
)


@given(
    # a list of lists that correspond to a sections of a top file
    sections=st.lists(
        st.lists(
            allowed_text,
            min_size=2,
            max_size=5,
        ),
        min_size=1,
        max_size=5,
    )
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_parser_invertible(sections, tmp_path):
    # flatten list of lists of strings to list of strings with subsection headers
    # use first element of each section as header
    testdir = setup_testdir(tmp_path)
    for s in sections:
        header = s[0]
        header = re.sub(r"\d", "x", header)
        s[0] = f"[ {header} ]"
    ls = [l for s in sections for l in s]
    print(ls)
    p = Path("topol.top")
    p2 = Path("topol2.top")
    p.parent.mkdir(exist_ok=True)
    with open(p, "w") as f:
        f.write("\n".join(ls))
    top = parsing.read_top(p)
    parsing.write_top(top, p2)
    top2 = parsing.read_top(p2)
    assert top == top2


@given(ls=st.lists(allowed_text))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_parser_fails_without_sections(ls, tmp_path):
    testdir = setup_testdir(tmp_path)
    p = Path("topol.top")
    p.parent.mkdir(exist_ok=True)
    with open(p, "w") as f:
        f.writelines(ls)
    with pytest.raises(ValueError):
        parsing.read_top(p)
    assert True


# %%
def test_parse_xml_ff(tmp_path):
    testdir = setup_testdir(tmp_path)
    ff_path = Path("amber99sb_trunc.xml")
    xml = parsing.read_xml_ff(ff_path)

    atomtypes = xml.find("AtomTypes")
    assert atomtypes is not None
    atomtypes.findall("Type")
    assert atomtypes.findall("Type")[0].attrib == {
        "class": "N",
        "element": "N",
        "mass": "14.00672",
    }
    assert atomtypes.findall("Type")[1].attrib == {
        "class": "H",
        "element": "H",
        "mass": "1.007947",
    }
