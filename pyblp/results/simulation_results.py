"""Economy-level structuring of BLP simulation results."""

import time
from typing import Dict, Hashable, List, Optional, Sequence, Tuple, TYPE_CHECKING, Union

import numpy as np

from .. import exceptions, options
from ..configurations.formulation import Formulation
from ..configurations.integration import Integration
from ..markets.simulation_results_market import SimulationResultsMarket
from ..moments import Moment, EconomyMoments
from ..utilities.basics import (
    Array, Error, SolverStats, generate_items, Mapping, output, RecArray, StringRepresentation, TableFormatter,
    format_seconds
)


# only import objects that create import cycles when checking types
if TYPE_CHECKING:
    from ..economies.problem import Problem  # noqa
    from ..economies.simulation import Simulation  # noqa


class SimulationResults(StringRepresentation):
    r"""Results of a solved simulation of synthetic BLP data.

    The :meth:`SimulationResults.to_problem` method can be used to convert the full set of simulated data and configured
    information into a :class:`Problem`.

    Attributes
    ----------
    simulation: `Simulation`
        :class:`Simulation` that created these results.
    product_data : `recarray`
        Simulated :attr:`Simulation.product_data` that are updated with synthetic prices and shares.
    delta : `ndarray`
        Simulated mean utility, :math:`\delta`.
    computation_time : `float`
        Number of seconds it took to compute synthetic prices and shares.
    fp_converged : `ndarray`
        Flags for convergence of the iteration routine used to compute synthetic prices in each market. Flags are in
        the same order as :attr:`Simulation.unique_market_ids`.
    fp_iterations : `ndarray`
        Number of major iterations completed by the iteration routine used to compute synthetic prices in each market.
        Counts are in the same order as :attr:`Simulation.unique_market_ids`.
    contraction_evaluations : `ndarray`
        Number of times the contraction used to compute synthetic prices was evaluated in each market. Counts are in the
        same order as :attr:`Simulation.unique_market_ids`.

    Examples
    --------
        - :doc:`Tutorial </tutorial>`

    """

    simulation: 'Simulation'
    product_data: RecArray
    delta: Array
    computation_time: float
    fp_converged: Array
    fp_iterations: Array
    contraction_evaluations: Array

    def __init__(
            self, simulation: 'Simulation', prices: Array, shares: Array, start_time: float, end_time: float,
            iteration_stats: Dict[Hashable, SolverStats]) -> None:
        """Structure simulation results."""
        self.simulation = simulation
        self.product_data = simulation.product_data.copy()
        self.product_data.prices = prices
        self.product_data.shares = shares
        self.delta = simulation._compute_true_X1({'prices': prices}) @ simulation.beta + simulation.xi
        self.computation_time = end_time - start_time
        self.fp_converged = np.array(
            [iteration_stats[t].converged for t in simulation.unique_market_ids], dtype=np.bool
        )
        self.fp_iterations = np.array(
            [iteration_stats[t].iterations for t in simulation.unique_market_ids], dtype=np.int
        )
        self.contraction_evaluations = np.array(
            [iteration_stats[t].evaluations for t in simulation.unique_market_ids], dtype=np.int
        )

    def __str__(self) -> str:
        """Format simulation results as a string."""
        header = [("Computation", "Time"), ("Fixed Point", "Iterations"), ("Contraction", "Evaluations")]
        widths = [max(len(k1), len(k2)) for k1, k2 in header]
        formatter = TableFormatter(widths)
        return "\n".join([
            "Simulation Results Summary:",
            formatter.line(),
            formatter([k[0] for k in header]),
            formatter([k[1] for k in header], underline=True),
            formatter([
                format_seconds(self.computation_time),
                self.fp_iterations.sum(),
                self.contraction_evaluations.sum()
            ]),
            formatter.line()
        ])

    def to_problem(
            self, product_formulations: Optional[Union[Formulation, Sequence[Optional[Formulation]]]] = None,
            product_data: Optional[Mapping] = None, agent_formulation: Optional[Formulation] = None,
            agent_data: Optional[Mapping] = None, integration: Optional[Integration] = None) -> 'Problem':
        """Convert the solved simulation into a problem.

        Parameters are the same as those of :class:`Problem`. By default, the structure of the problem will be the same
        as that of the solved simulation.

        Parameters
        ----------
        product_formulations : `Formulation or tuple of Formulation, optional`
            By default, :attr:`Simulation.product_formulations`.
        product_data : `structured array-like, optional`
            By default, :attr:`SimulationResults.product_data`.
        agent_formulation : `Formulation, optional`
            By default, :attr:`Simulation.agent_formulation`.
        agent_data : `structured array-like, optional`
            By default, :attr:`Simulation.agent_data`.
        integration : `Integration, optional`
            By default, this is unspecified.

        Returns
        -------
        `Problem`
            A BLP problem.

        Examples
        --------
            - :doc:`Tutorial </tutorial>`

        """
        from ..economies.problem import Problem  # noqa
        if product_formulations is None:
            product_formulations = self.simulation.product_formulations
        if product_data is None:
            product_data = self.product_data
        if agent_formulation is None:
            agent_formulation = self.simulation.agent_formulation
        if agent_data is None:
            agent_data = self.simulation.agent_data
        assert product_formulations is not None and product_data is not None
        return Problem(product_formulations, product_data, agent_formulation, agent_data, integration)

    def compute_micro(self, micro_moments: Sequence[Moment]) -> Array:
        r"""Compute averaged micro moment values, :math:`\bar{g}_M`.

        Typically, this method is used to compute the values that micro moments aim to match. This can be done by
        setting ``value=0`` in each of the configured ``micro_moments``.

        Parameters
        ----------
        micro_moments : `tuple of ProductsAgentsCovarianceMoment`
            Configurations for the averaged micro moments that will be computed. The only type of micro moment currently
            supported is the :class:`ProductsAgentsCovarianceMoment`.

        Returns
        -------
        `ndarray`
            Averaged micro moments, :math:`\bar{g}_M`, in :eq:`averaged_micro_moments`.

        Examples
        --------
            - :doc:`Tutorial </tutorial>`

        """
        errors: List[Error] = []

        # keep track of long it takes to compute micro moments
        output("Computing micro moment values ...")
        start_time = time.time()

        # validate and structure micro moments before outputting related information
        moments = EconomyMoments(self.simulation, micro_moments)
        if moments.MM == 0:
            raise ValueError("At least one micro moment should be specified.")
        output("")
        output(moments.format("Micro Moments"))

        # define a factory for computing market-level micro moments
        def market_factory(s: Hashable) -> Tuple[SimulationResultsMarket]:
            """Build a market along with arguments used to compute micro moments."""
            data_override_cs = {
                'prices': self.product_data.prices[self.simulation._product_market_indices[s]],
                'shares': self.product_data.shares[self.simulation._product_market_indices[s]]
            }
            market_s = SimulationResultsMarket(
                self.simulation, s, self.simulation._parameters, self.simulation.sigma, self.simulation.pi,
                self.simulation.rho, self.simulation.beta, self.delta, moments, data_override_cs
            )
            return market_s,

        # compute micro moments (averaged across markets) market-by-market
        micro = np.zeros((moments.MM, 1), options.dtype)
        generator = generate_items(
            self.simulation.unique_market_ids, market_factory, SimulationResultsMarket.safely_compute_micro
        )
        for t, (micro_t, errors_t) in generator:
            indices = moments.market_indices[t]
            micro[indices] += micro_t / moments.market_counts[indices]
            errors.extend(errors_t)

        # output a warning about any errors
        if errors:
            output("")
            output(exceptions.MultipleErrors(errors))
            output("")

        # output how long it took to compute the micro moments
        end_time = time.time()
        output("")
        output(f"Finished after {format_seconds(end_time - start_time)}.")
        output("")
        return micro
