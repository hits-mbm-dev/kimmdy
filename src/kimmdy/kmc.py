"""
Kinetic Monte Carlo (KMC) classes and functions.

In our system, the reaction rate r = (deterministic) reaction constant k
= stochastic reaction constant c (from gillespie 1977)
= propensity a (from Anderson 2007)
because of the fundamental premise of chemical kinetics
and because we have one reactant molecule
"""

import logging
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Optional

import numpy as np
from numpy.random import default_rng

from kimmdy.recipe import Recipe, RecipeCollection


@dataclass
class KMCResult:
    """The result of a KMC step. Similar to a Recipe but for the concrete realization of a reaction.

    Attributes
    ----------
    recipe
        Single sequence of RecipeSteps to build product
    reaction_probability
        Integral of reaction propensity with respect to time
    time_delta
        MC time jump during which the reaction occurs [ps]
    time_start
        Time, from which the reaction starts. The reaction changes the
        geometry/topology of this timestep and continues from there. [ps]
    """

    recipe: Recipe = field(default_factory=lambda: Recipe([], [], []))
    reaction_probability: Optional[list[float]] = None
    time_delta: Optional[float] = None
    time_start: Optional[float] = None


def rf_kmc(
    recipe_collection: RecipeCollection,
    logger: logging.Logger = logging.getLogger(__name__),
    rng: np.random.Generator = default_rng(),
) -> KMCResult:
    """Rejection-Free Monte Carlo.
    Takes RecipeCollection and choses a recipe based on the relative propensity of the events.
    The 'start' time of the reaction is the time of the highest rate of the accepted reaction.

    Compare e.g. [Wikipedia KMC - rejection free](https://en.wikipedia.org/wiki/Kinetic_Monte_Carlo#Rejection-free_KMC)

    Parameters
    ---------
    recipe_collection
        from which one will be choosen
    rng
        function to generate random numbers in the KMC step
    """

    logger.debug(f"Start KMC, {len(recipe_collection.recipes)} recipes")

    # check for empty ReactionResult
    if len(recipe_collection.recipes) == 0:
        logger.warning("Empty ReactionResult; no reaction chosen")
        return KMCResult()

    # 0. Initialization
    reaction_probability = []

    # 1. Calculate the probability for each reaction

    for recipe in recipe_collection.recipes:
        dt = [x[1] - x[0] for x in recipe.timespans]
        t_interval = recipe.timespans[-1][-1] - recipe.timespans[0][0]
        reaction_probability.append(sum(np.multiply(dt, recipe.rates)) / t_interval)

    # 2. Set the total rate to the sum of individual rates
    probability_cumulative = np.cumsum(reaction_probability)
    probability_sum = probability_cumulative[-1]

    # 3. Generate two independent uniform (0,1) random numbers u1,u2
    u = rng.random(2)
    logger.debug(
        f"\tRandom values u: {u}, number cumulative probabilities "
        f"{len(probability_cumulative)}, probability sum {probability_sum}"
    )

    # 4. Find the even to carry out, mu, using binary search (np.searchsorted)
    pos = np.searchsorted(probability_cumulative, u[0] * probability_sum)
    recipe = recipe_collection.recipes[pos]
    reaction_time = recipe.timespans[np.argmax(recipe.rates)][1]
    logger.info(f"Chosen Recipe: {recipe} at time {reaction_time}")

    # 5. Calculate the time step associated with mu
    time_delta = np.log(1 / u[1]) / probability_sum
    logger.info(f"Time delta: {time_delta}\nprobability {reaction_probability}")

    return KMCResult(
        recipe=recipe,
        reaction_probability=reaction_probability,
        time_delta=time_delta,
        time_start=reaction_time,
    )


def frm(
    recipe_collection: RecipeCollection,
    logger: logging.Logger = logging.getLogger(__name__),
    rng: np.random.Generator = default_rng(),
    MD_time: Optional[float] = None,
) -> KMCResult:
    """First Reaction Method variant of Kinetic Monte Carlo.
    takes RecipeCollection and choses a recipe based on which reaction would occur.

    Compare e.g. [Wikipedia KMC - time dependent](https://en.wikipedia.org/wiki/Kinetic_Monte_Carlo#Time-dependent_Algorithms)

    Parameters
    ---------
    recipe_collection
        from which one will be choosen
    rng
        to generate random numbers in the KMC step
    MD_time
        time [ps] to compare conformational events with reaction events in the time domain
    """

    # TODO:
    # Currently, this looks at the complete traj and says something happens in this
    # integral or not. It should split it into smaller junks, since the rates
    # fluctuate. Then, still a whole integral is accepted, but one integral does not
    # contain many different conformations.

    # check for empty ReactionResult
    if len(recipe_collection.recipes) == 0:
        logger.warning("Empty ReactionResult; no reaction chosen")
        return KMCResult()

    # 0. Initialization
    reaction_probability = []
    # empty recipe for continuing the MD
    recipes = [Recipe([], [], [])] if MD_time else []
    tau = [MD_time] if MD_time else []
    # conformational change is an event that occurs during MD time
    # 1. Generate M independent uniform (0,1) random numbers
    u = rng.random(len(recipe_collection.recipes))
    # 2. Calculate the cumulative probabilities for each event
    for i, recipe in enumerate(recipe_collection.recipes):
        dt = [x[1] - x[0] for x in recipe.timespans]
        cumulative_probability = np.cumsum(
            list(map(lambda x, y: x * y, dt, recipe.rates))
        )
        reaction_probability.append(cumulative_probability[-1])
        # 3. For each event k, find the time until the this reaction takes place tau_k,
        #    if it is during the time when the propensities are defined (simulation time)
        if (p := np.log(1 / u[i])) <= cumulative_probability[-1]:
            # this means a reaction will take place during the defined time
            pos_time = np.searchsorted(cumulative_probability, p)
            tau.append(recipe.timespans[pos_time][1])
            recipes.append(recipe)
    # 4. Find the event whose putative time is least
    try:
        pos_event = np.argmin(tau)
        chosen_recipe = recipes[pos_event]
        time_delta = tau[pos_event]
        reaction_time = chosen_recipe.timespans[np.argmax(chosen_recipe.rates)][1]
    except ValueError:
        logger.warning(
            f"FRM recipe selection did not work, probably tau: {tau} is empty."
        )
        return KMCResult()

    return KMCResult(
        recipe=chosen_recipe,
        reaction_probability=reaction_probability,
        time_delta=time_delta,
        time_start=reaction_time,
    )


def extrande_mod(
    recipe_collection: RecipeCollection,
    tau_scale: float,
    logger: logging.Logger = logging.getLogger(__name__),
    rng: np.random.Generator = default_rng(),
) -> KMCResult:
    """Modified Extrande KMC

    Improved implementation of
    `Stochastic Simulation of Biomolecular Networks in Dynamic Environments`
    [10.1371/journal.pcbi.1004923](https://doi.org/10.1371/journal.pcbi.1004923)
    Changes: The considered time window is chosen to be a window containing
    constant rates. This prevents very small tau caused by a spike in the rate
    at a later point. As a side effect, the upper rate bound b and current rate
    a0 are the same, and the 'extra' side channel can not be triggered anymore.

    This should be more efficient given a limited number of time windows
    containing constant rates.

    Parameters
    ----------
    recipe_collection
        from which one will be choosen
    rng
        function to generate random numbers in the KMC step
    tau_scale
        Scaling factor for tau, by default 1.0

    Returns
    -------
    KMCResult
        time delta set to 0
    """

    # check for empty ReactionResult
    if len(recipe_collection.recipes) == 0:
        logger.warning("Empty ReactionResult; no reaction chosen")
        return KMCResult()

    # initialize
    t = 0
    n_extra = 0
    rejected = 0
    chosen_recipe = None

    boarders, rate_windows, recipe_windows = recipe_collection.calc_ratesum()

    rate_sums = [float(sum(l)) for l in rate_windows]
    idx_rate_max = np.argmax(rate_sums)
    expected_tau = np.mean(tau_scale * rng.exponential(1 / max(rate_sums), 50))
    logger.debug(
        f"Max of summed rate of {rate_sums[idx_rate_max]:.2} between t "
        f"{boarders[idx_rate_max]} - {boarders[idx_rate_max+1]}\n"
        f"\t\texpected tau: {expected_tau}"
    )

    b = None
    tau = None
    l = None
    t_max = None
    for cr_boarders, cr_rates, cr_recipes in zip(
        pairwise(boarders), rate_windows, recipe_windows
    ):
        t_max = cr_boarders[1]
        if len(cr_rates) == 0:
            logger.debug(f"No rates in window {cr_boarders}.")
            t = t_max
            rejected += 1
            continue
        rate_cumsum = np.cumsum(cr_rates)
        b = rate_cumsum[-1]
        tau = tau_scale * rng.exponential(1 / b)

        while t < t_max:
            l = t_max - t
            if tau > l:
                # reject, goto next window
                logger.debug(f"Tau exceeded window {cr_boarders}.")
                t = t_max
                rejected += 1
                continue

            # Accept time, chose reaction
            t += tau
            u = rng.random()
            idx = np.searchsorted(rate_cumsum, b * u)
            chosen_recipe = cr_recipes[idx]
            break

        if chosen_recipe is not None:
            break

    if chosen_recipe is None:
        logger.info(
            f"No reaction was chosen\naccepted: 0, rejected: {rejected}, extra: {n_extra}"
        )
        logger.debug(
            f"Extrande stats:\n\tb:\t\t{b}"
            f"\n\tTau:\t{tau}\n\tl:\t\t{l}\n\tt:\t\t{t}\n\tt_max:\t{t_max}"
        )
        return KMCResult()

    logger.info(
        f"Reaction {chosen_recipe.get_recipe_name()} was chose at time {t}\n"
        f"accepted: 1, rejected: {rejected}, extra: {n_extra}"
    )
    logger.debug(
        f"Extrande stats:\n\tb:\t\t{b}"
        f"\n\tTau:\t{tau}\n\tl:\t\t{l}\n\tt:\t\t{t}\n\tt_max:\t{t_max}"
    )

    return KMCResult(
        recipe=chosen_recipe,
        reaction_probability=None,
        time_delta=0,  # instantaneous reaction
        time_start=t,
    )


def extrande(
    recipe_collection: RecipeCollection,
    tau_scale: float,
    logger: logging.Logger = logging.getLogger(__name__),
    rng: np.random.Generator = default_rng(),
) -> KMCResult:
    """Extrande KMC

    Implemented as in
    `Stochastic Simulation of Biomolecular Networks in Dynamic Environments`
    [10.1371/journal.pcbi.1004923](https://doi.org/10.1371/journal.pcbi.1004923)

    Parameters
    ----------
    recipe_collection
        from which one will be choosen
    rng
        function to generate random numbers in the KMC step
    tau_scale
        Scaling factor for tau, by default 1.0

    Returns
    -------
    KMCResult
        time delta set to 0
    """

    # check for empty ReactionResult
    if len(recipe_collection.recipes) == 0:
        logger.warning("Empty ReactionResult; no reaction chosen")
        return KMCResult()

    # initialize t
    t = 0
    a0 = -1
    u = -1
    b = -1
    chosen_recipe = None

    # Find L and B
    boarders, rate_windows, recipe_windows = recipe_collection.calc_ratesum()
    # time of last rate, should be last MD time
    t_max = boarders[-1]

    rate_sums = [float(sum(l)) for l in rate_windows]
    idx_rate_max = np.argmax(rate_sums)
    expected_tau = np.mean(tau_scale * rng.exponential(1 / max(rate_sums), 10))
    logger.debug(
        f"Max of summed rate of {rate_sums[idx_rate_max]:.2} between t "
        f"{boarders[idx_rate_max]} - {boarders[idx_rate_max+1]}\n"
        f"\t\texpected tau: {expected_tau}"
    )

    n_extra = 0
    tau = None
    l = None
    while t < t_max:
        crr_window_idx = np.searchsorted(boarders, t, side="right") - 1
        # only 0 rates left -> skip to end
        if not any([r > 0 for r in rate_sums[crr_window_idx:]]):
            t = t_max
            break

        b = max(rate_sums[crr_window_idx:])
        l = t_max - t

        tau = tau_scale * rng.exponential(1 / b)
        if tau > l:
            # reject
            logger.info(
                "Tau exceeded simulation frame, no reaction to perform.\n"
                f"accepted: 0, rejected: 1, extra: {n_extra}"
            )
            logger.debug(
                f"Extrande stats:\n\tb:\t\t{b}\n\tTau:\t{tau}"
                f"\n\tl:\t\t{l}\n\tt:\t\t{t}\n\tt_max:\t{t_max}"
            )
            return KMCResult()

        t += tau
        new_window_idx = np.searchsorted(boarders, t, side="right") - 1
        rate_cumsum = np.cumsum(rate_windows[new_window_idx])
        # catch landing in window with no recipes -> next window
        if len(rate_cumsum) == 0:
            t = boarders[crr_window_idx + 1]
            logger.debug(f"Jumped to frame with no recip, new t={t}")
            continue
        a0 = rate_cumsum[-1]

        u = rng.random()
        if a0 >= b * u:
            # Accept time, chose reaction
            idx = np.searchsorted(rate_cumsum, b * u)
            chosen_recipe = recipe_windows[new_window_idx][idx]
            break

        # Extra reaction channel, repeat for new t
        n_extra += 1
        if n_extra == len(boarders * 1000):
            logger.warning(
                f"{n_extra} extra reactions performed during extrande KMC calculation. Try increasing tau_scale"
            )

    if None in [tau, l]:
        logger.error(f"Extrande calculation failed, some variables are None.")

    if chosen_recipe is None:
        logger.info(
            f"No reaction was chosen\naccepted: 0, rejected: 1, extra: {n_extra}"
        )
        logger.debug(
            f"Extrande stats:\n\ta0:\t\t{a0}\n\tb:\t\t{b}\n\tb*u:\t{b*u}"
            f"\n\tTau:\t{tau}\n\tl:\t\t{l}\n\tt:\t\t{t}\n\tt_max:\t{t_max}"
        )
        return KMCResult()

    logger.info(
        f"Reaction {chosen_recipe.get_recipe_name()} was chose at time {t}\n"
        f"accepted: 1, rejected: 0, extra: {n_extra}"
    )
    logger.debug(
        f"Extrande stats:\n\ta0:\t\t{a0}\n\tb:\t\t{b}\n\tb*u:\t{b*u}"
        f"\n\tTau:\t{tau}\n\tl:\t\t{l}\n\tt:\t\t{t}\n\tt_max:\t{t_max}"
    )
    return KMCResult(
        recipe=chosen_recipe,
        reaction_probability=None,
        time_delta=0,  # instantaneous reaction
        time_start=t,
    )
