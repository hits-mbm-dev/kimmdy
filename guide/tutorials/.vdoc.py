# type: ignore
# flake8: noqa
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#| code-fold: true
#| code-summary: "KIMMDY config file (kimmdy.yml)"

# yaml-language-server: $schema=../../src/kimmdy/kimmdy-yaml-schema.json

dryrun: false
name: 'hexalanine_homolysis_000'
max_tasks: 100
gromacs_alias: 'gmx'
gmx_mdrun_flags: -maxh 24 -dlb yes -nt 8
ff: 'amber99sb-star-ildnp.ff' # optional, dir endinng with .ff by default 
top: 'hexala_out.top'
gro: 'npt.gro'
ndx: 'index.ndx'
plumed: 'plumed.dat'
mds:
  equilibrium:
    mdp: 'md.mdp'
  pull:
    mdp: 'md.mdp'
    use_plumed: true
  relax:
    mdp: 'md_slow_growth.mdp'
changer:
  coordinates:
    md: 'relax'
    slow_growth: True
  topology:
    parameterization: 'basic' 
reactions:
  homolysis:
    edis: 'edissoc.dat'
    itp: 'ffbonded.itp'
sequence:
  - equilibrium
  - pull  
  - reactions
  - equilibrium
  - pull

#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#| code-fold: true
#| code-summary: "KIMMDY config file (kimmdy.yml)"

# yaml-language-server: $schema=../../src/kimmdy/kimmdy-yaml-schema.json

dryrun: false
name: 'fibril_000'
max_tasks: 100
gromacs_alias: 'gmx'
gmx_mdrun_flags: -maxh 24 -dlb yes -ntomp 4 -ntmpi 10  -pme gpu -bonded gpu -npme 1
ff: 'amber99sb-star-ildnp.ff' # optional, dir endinng with .ff by default 
top: 'hexala_out.top'
gro: 'npt.gro'
ndx: 'index.ndx'
plumed: 'plumed.dat'
mds:
  equilibrium:
    mdp: 'md.mdp'
  pull:
    mdp: 'md.mdp'
    use_plumed: true
  relax:
    mdp: 'md_slow_growth.mdp'
changer:
  coordinates:
    md: 'relax'
    slow_growth: True
  topology:
    parameterization: 'basic' 
reactions:
  homolysis:
    edis: 'edissoc.dat'
    itp: 'ffbonded.itp'
sequence:
  - equilibrium
  - pull  
  - reactions
  - equilibrium
  - pull

#
#
#
#
#
#
#
