"""
The Runmanager is the main entry point of the program.

It manages the queue of tasks, communicates with the
rest of the program and keeps track of global state.
"""
from __future__ import annotations
import logging
from pathlib import Path
from copy import deepcopy
import dill
import queue
from enum import Enum, auto
from typing import Callable
from kimmdy.config import Config
from kimmdy.utils import increment_logfile
from kimmdy.parsing import read_top
from kimmdy.reaction import ReactionPlugin, RecipeCollection
import kimmdy.changemanager as changer
from kimmdy.tasks import Task, TaskFiles, TaskMapping
from kimmdy.utils import run_shell_cmd, run_gmx
from pprint import pformat
from kimmdy import plugins
from kimmdy.topology.topology import Topology
from kimmdy.kmc import rf_kmc

# file types of which there will be multiple files per type
AMBIGUOUS_SUFFS = ["dat", "xvg", "log", "itp", "mdp"]
# are there cases where we have multiple trr files?


class State(Enum):
    """State of the system.
    one of IDLE, MD, REACTION, DONE.
    """

    IDLE = auto()
    MD = auto()
    REACTION = auto()
    DONE = auto()


def get_existing_files(config: Config):
    """Initialize latest_files with every existing file defined in config"""
    file_d = {}
    attr_names = filter(lambda s: s[0] != "_", config.__dir__())
    for attr_name in attr_names:
        attr = getattr(config, attr_name)
        if isinstance(attr, Path):
            if not attr.exists():
                continue
            key = attr.suffix[1:]  # rm the get_existing_files
            # AMBIGUOUS_SUFFS -> key whole name
            if attr.suffix[1:] in AMBIGUOUS_SUFFS:
                key = attr.name
            file_d[key] = attr
        elif isinstance(attr, Config):
            file_d.update(get_existing_files(attr))
    return file_d


class RunManager:
    """The Runmanager is the main entry point of the program.

    Manages the queue of tasks, communicates with the
    rest of the program and keeps track of global state.

    Attributes
    ----------
    config :
        The configuration object.
    from_checkpoint :
        Whether the runmanager was initialized from a checkpoint.
    tasks :
        Tasks from config.
    crr_tasks :
        Current tasks.
    iteration :
        Current iteration.
    iterations :
        Total number of iterations.
    state :
        Current state of the system.
    recipe_collection :
        Collection of recipes.
    latest_files :
        Dictionary of latest files.
    histfile :
        Path to history file.
    cptfile :
        Path to checkpoint file.
    ffpatch :
        Path to force field patch file.
    top :
        Topology object.
    filehist :
        List of dictionaries of TaskFiles.
    task_mapping :
        Mapping of task names to runmanager methods.
    """

    def __init__(self, config: Config):
        self.config: Config = config
        self.from_checkpoint: bool = False
        self.tasks: queue.Queue[Task] = queue.Queue()  # tasks from config
        self.crr_tasks: queue.Queue[Task] = queue.Queue()  # current tasks
        self.iteration: int = 0
        self.iterations: int = self.config.iterations
        self.state: State = State.IDLE
        self.recipe_collection: RecipeCollection = RecipeCollection([])
        self.latest_files: dict[str, Path] = get_existing_files(config)
        logging.debug("Initialized latest files:")
        logging.debug(pformat(self.latest_files))
        self.histfile: Path = increment_logfile(Path(f"{self.config.out}_history.log"))
        self.cptfile: Path = increment_logfile(Path(f"{self.config.out}_kimmdy.cpt"))
        try:
            _ = self.config.ffpatch
        except AttributeError:
            self.config.ffpatch = None
        self.top = Topology(read_top(self.config.top), self.config.ffpatch)

        self.filehist: list[dict[str, TaskFiles]] = [
            {"setup": TaskFiles(self.get_latest)}
        ]

        self.task_mapping: TaskMapping = {
            "md": self._run_md,
            "reactions": [
                self._query_reactions,
                self._decide_reaction,
                self._run_recipe,
            ],
            "breakpoint": [self._breakpoint],
        }

        # Instantiate reactions
        self.reaction_plugins: list[ReactionPlugin] = []
        react_names = self.config.reactions.get_attributes()
        # logging.info("Instantiating Reactions:", *react_names)
        for rp_name in react_names:
            r = plugins[rp_name]
            reaction_plugin: ReactionPlugin = r(rp_name, self)
            self.reaction_plugins.append(reaction_plugin)

        logging.debug("Configuration from input file:")
        logging.debug(pformat(self.config.__dict__))

    def run(self):
        logging.info("Start run")
        logging.info("Build task list")

        if not self.from_checkpoint:
            # allows for mapping one config entry to multiple tasks
            for entry in self.config.sequence:
                if entry in self.config.mds.get_attributes():
                    task = self.task_mapping["md"]
                    logging.info(f"Put Task: {task}")
                    self.tasks.put(Task(task, kwargs={"instance": entry}))
                else:
                    for task in self.task_mapping[entry]:
                        logging.info(f"Put Task: {task}")
                        self.tasks.put(Task(task))

        while not (self.state is State.DONE or self.iteration >= self.iterations):
            logging.info("Write checkpoint before next task")
            with open(self.cptfile, "wb") as f:
                dill.dump(self, f)
            next(self)

        logging.info(
            f"Finished running tasks, state: {self.state}, "
            f"iteration:{self.iteration}, max:{self.iterations}"
        )

    def get_latest(self, suffix: str):
        """Returns path to latest file of given type.

        For .dat files (in general ambiguous extensions) use full file name.
        Errors if file is not found.
        """
        logging.debug("Getting latest suffix: " + suffix)
        try:
            path = self.latest_files[suffix]
            logging.debug("Found: " + str(path))
            return path
        except Exception:
            m = f"File {suffix} requested but not found!"
            logging.error(m)
            raise FileNotFoundError(m)

    def __iter__(self):
        return self

    def __next__(self):
        if self.tasks.empty() and self.crr_tasks.empty():
            self.state = State.DONE
            return
        if not self.crr_tasks.empty():
            task = self.crr_tasks.get()
        else:
            task = self.tasks.get()
            self.iteration += 1
        if self.config.dryrun:
            logging.info(f"Pretend to run: {task.name} with args: {task.kwargs}")
            return
        logging.info("Starting task: " + pformat(task))
        files = task()
        self._discover_output_files(task.name, files)

    def _discover_output_files(self, taskname, files: TaskFiles):
        """Discover further files written by a task.

        and add those files to the `files` as well as
        the file history and latest files.
        """
        # discover other files written by the task
        if hasattr(files, "outputdir"):
            for path in files.outputdir.iterdir():
                suffix = path.suffix[1:]
                if suffix in AMBIGUOUS_SUFFS:
                    suffix = path.name
                if files.output.get(suffix) is not None:
                    if files.output[suffix] == files.outputdir / path:
                        logging.debug(
                            "_discover_output_files wants to overwrite files.output!"
                        )
                        logging.debug(f"Suffix {suffix}")
                        logging.debug(f"From {files.output[suffix]}")
                        logging.debug(f"To {files.outputdir / path}")
                    continue
                files.output[suffix] = files.outputdir / path

            logging.debug("Update latest files with: ")
            logging.debug(pformat(files.output))
            self.latest_files.update(files.output)
            logging.debug("Append to file history")
            self.filehist.append({taskname: files})

            logging.info(f"Current task files:")
            m = f"""
            Task: {taskname} with output directory: {files.outputdir}
            Task: {taskname}, input:\n{pformat(files.input)}
            Task: {taskname}, output:\n{pformat(files.output)}
            """
            logging.info(m)
            with open(self.histfile, "a") as f:
                f.write(m)

    def _create_task_directory(self, postfix: str) -> TaskFiles:
        """Creates TaskFiles object, output directory and symlinks ff."""
        files = TaskFiles(self.get_latest)
        files.outputdir = self.config.out / f"{self.iteration}_{postfix}"
        files.outputdir.mkdir(exist_ok=self.from_checkpoint)
        if not (files.outputdir / self.config.ff.name).exists():
            (files.outputdir / self.config.ff.name).symlink_to(self.config.ff)
        return files

    def _dummy(self):
        logging.info("Start dummy task")
        files = TaskFiles(self.get_latest)
        files.outputdir = self.config.out / f"{self.iteration}_dummy"
        files.outputdir.mkdir()
        run_shell_cmd("pwd>./pwd.pwd", files.outputdir)
        return files

    def _run_md(self, instance) -> TaskFiles:
        """General MD simulation"""
        logging.info(f"Start MD {instance}")
        self.state = State.MD

        files = self._create_task_directory(f"{instance}")
        md_config = self.config.mds.attr(instance)
        gmx_alias = self.config.gromacs_alias

        logging.warning(self.latest_files)
        top = files.input["top"]
        gro = files.input["gro"]
        mdp = md_config.mdp
        ndx = files.input["ndx"]

        outputdir = files.outputdir
        # make maxh and ntomp accessible?
        maxh = 24
        ntomp = 2

        grompp_cmd = (
            f"{gmx_alias} grompp -p {top} -c {gro} "
            f"-f {mdp} -n {ndx} -o {instance}.tpr -maxwarn 5"
        )
        # only appends these lines if there are trr and edr files
        try:
            trr = files.input["trr"]
            edr = files.input["edr"]
            grompp_cmd += f" -t {trr} -e {edr}"
        except FileNotFoundError:
            pass

        mdrun_cmd = (
            f"{gmx_alias} mdrun -s {instance}.tpr -cpi {instance}.cpt "
            f"-x {instance}.xtc -o {instance}.trr -cpo {instance}.cpt "
            f"-c {instance}.gro -g {instance}.log -e {instance}.edr "
            f"-px {instance}_pullx.xvg -pf {instance}_pullf.xvg "
            f"-ro {instance}-rotation.xvg -ra {instance}-rotangles.log "
            f"-rs {instance}-rotslabs.log -rt {instance}-rottorque.log "
            f"-maxh {maxh} -dlb yes "
        )
        # -ntomp {ntomp} removed for now
        # like this, the previous checkpoint file would not be used,
        # -t and -e options from grompp
        # replace the checkpoint file if gen_vel = no in the mdp file

        if "plumed" in md_config.get_attributes():
            mdrun_cmd += f" -plumed {md_config.plumed.dat}"
            files.output = {"plumed.dat": md_config.plumed.dat}
            # add plumed.dat to output to indicate it as current plumed.dat file

        # specify trr to prevent rotref trr getting set as standard trr
        files.output["trr"] = files.outputdir / f"{instance}.trr"
        logging.debug(f"grompp cmd: {grompp_cmd}")
        logging.debug(f"mdrun cmd: {mdrun_cmd}")
        run_gmx(grompp_cmd, outputdir)
        run_gmx(mdrun_cmd, outputdir)

        logging.info(f"Done with MD {instance}")
        return files

    def _query_reactions(self):
        logging.info("Query reactions")
        self.state = State.REACTION
        # empty list for every new round of queries
        self.recipe_collection: RecipeCollection = RecipeCollection([])

        for reaction_plugin in self.reaction_plugins:
            # TODO: refactor into Task
            files = self._create_task_directory(reaction_plugin.name)

            self.recipe_collection.recipes.extend(
                reaction_plugin.get_recipe_collection(files).recipes
            )

        logging.info("Reaction done")
        return files

    def _decide_reaction(
        self,
        decision_strategy: Callable[[RecipeCollection], dict] = rf_kmc,
    ):
        logging.info("Decide on a recipe")
        logging.debug(f"Available reaction results: {self.recipe_collection}")
        decision_d = decision_strategy(self.recipe_collection)
        self.recipe_steps = decision_d.recipe_steps
        logging.info("Chosen recipe is:")
        logging.info(self.recipe_steps)
        return

    def _run_recipe(self) -> TaskFiles:
        logging.info(f"Start Recipe in KIMMDY iteration {self.iteration}")
        logging.info(f"Recipe: {self.recipe_steps}")

        files = self._create_task_directory("recipe")

        files.output = {"top": files.outputdir / "topol_mod.top"}
        logging.debug(f"Chose recipe: {self.recipe_steps}")

        # changes to topology
        top_prev = deepcopy(self.top)
        changer.modify_top(
            self.recipe_steps,
            files,
            self.config.ffpatch,
            self.top,
        )
        logging.info(f'Wrote new topology to {files.output["top"].parts[-3:]}')

        # changes to plumed.dat
        if "plumed.dat" in self.latest_files:
            files.output["plumed.dat"] = files.outputdir / "plumed_mod.dat"
            changer.modify_plumed(
                self.recipe_steps,
                files.input["plumed.dat"],
                files.output["plumed.dat"],
                files.input["distances.dat"],
            )
            logging.info(
                f'Wrote new plumedfile to {files.output["plumed.dat"].parts[-3:]}'
            )

        # changes to coordinates
        run_parameter_growth, top_merge_path = changer.modify_coords(
            self.recipe_steps, files, top_prev, deepcopy(self.top)
        )
        instance = None

        if run_parameter_growth:
            if hasattr(self.config.changer.coordinates, "md_parameter_growth"):
                # just to make pyright happy
                if top_merge_path:
                    self.latest_files["top"] = top_merge_path
                instance = self.config.changer.coordinates.md_parameter_growth

            else:
                logging.warning(
                    f"No parameter growth MD possible, trying classical MD relaxation."
                )
                run_parameter_growth = False
        else:
            logging.info(f'Wrote new coordinates to {files.output["trr"].parts[-3:]}')

        if not run_parameter_growth:
            if hasattr(self.config.changer.coordinates, "md"):
                instance = self.config.changer.coordinates.md
            else:
                logging.info(f"No MD relaxation after reaction.")

        if instance:
            # crr_task nested in this task instead of running afterwards
            self.crr_tasks.put(
                Task(
                    self._run_md,
                    kwargs={"instance": instance},
                )
            )
            next(self)

        logging.info("Reaction done")
        return files

    def _breakpoint(self):
        breakpoint()
