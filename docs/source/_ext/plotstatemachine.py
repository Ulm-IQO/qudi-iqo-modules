import os
import importlib
from typing import Tuple

from docutils import nodes

from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective, SphinxRole
from sphinx.util.typing import ExtensionMetadata

from fysom import Fysom
import pygraphviz

class GraphDirective(SphinxDirective):
    """A directive to plot the graph of a state machine."""
    required_arguments = 1
    def run(self) -> list[nodes.Node]:
        qualifier = self.arguments[0]
        modulename,classname = qualifier.rsplit(sep='.', maxsplit=1)
        module = importlib.import_module(modulename)
        cls = getattr(module, classname)
        fsm = Fysom({
            'initial': cls._initial_state,
            'events': cls._SampledFiniteStateInterface__fysom_events_structure,
        })
        graph = pygraphviz.AGraph(directed=True, id="statemachine_"+classname, name="statemachine_"+classname)
        for state in cls._SampledFiniteStateInterface__fysom_states:
            method = getattr(cls, state, None)
            graph.add_node(state, 
                           label=state.replace("_", " "), 
                           tooltip=method.__doc__, 
                           URL=f"#{modulename}.{qualifier}.{classname}.{state}"
            )
        for event, transitions in fsm._map.items():
            for from_state, to_state in transitions.items():
                if from_state == "none":
                    from_state = "start"
                if from_state == "*":
                    for from_state in cls._SampledFiniteStateInterface__fysom_states:
                        graph.add_edges_from([(from_state, to_state)], label=event)
                else:
                    graph.add_edges_from([(from_state, to_state)], label=event)

        graph.layout(prog='dot')
        mapfile = graph.draw(format="cmapx")
        svgfile = graph.draw(format="svg_inline")
        image_node = nodes.raw('', svgfile.decode('utf8'), format='html')
        raw_node = nodes.raw('', mapfile.decode('utf8'), format='html')
        return [image_node, raw_node]

def setup(app: Sphinx) -> ExtensionMetadata:
    app.add_directive('statemachine', GraphDirective)
    return {
        'version': '0.1',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
