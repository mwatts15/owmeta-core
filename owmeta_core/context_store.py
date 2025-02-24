from itertools import chain

from rdflib.store import Store, VALID_STORE, NO_STORE
try:
    from rdflib.plugins.stores.memory import Memory
except ImportError:
    # rdflib<6.0.0
    from rdflib.plugins.memory import IOMemory as Memory

from rdflib.term import Variable

from .context_common import CONTEXT_IMPORTS
from .rdf_utils import transitive_lookup


class ContextStoreException(Exception):
    pass


class ContextStore(Store):
    '''
    A store specific to a `~owmeta_core.context.Context`


    A `ContextStore` may have triples
    '''

    context_aware = True

    def __init__(self, context=None, include_stored=False, imports_graph=None, **kwargs):
        """
        Parameters
        ----------
        context : ~owmeta_core.context.Context
            The context to which this store belongs
        include_stored : bool
            If `True`, the backing store will be queried as well as the staged triples in
            `context`
        imports_graph : ~rdflib.store.Store or ~rdflib.graph.Graph
            The graph to query for imports relationships between contexts
        **kwargs
            Passed on to `Store <rdflib.store.Store.__init__>`
        """
        super(ContextStore, self).__init__(**kwargs)
        self._memory_store = None
        self._include_stored = include_stored
        self._imports_graph = imports_graph
        if context is not None:
            self._init_store(context)

    def open(self, configuration, create=False):
        if self.ctx is not None:
            return VALID_STORE
        else:
            return NO_STORE

    def _init_store(self, ctx):
        self.ctx = ctx

        if self._include_stored:
            self._store_store = RDFContextStore(ctx, imports_graph=self._imports_graph)
        else:
            self._store_store = None

        if self._memory_store is None:
            self._memory_store = Memory()
            self._init_store0(ctx)

    def _init_store0(self, ctx, seen=None):
        if seen is None:
            seen = set()
        ctxid = ctx.identifier
        if ctxid in seen:
            return
        seen.add(ctxid)
        self._memory_store.addN((s, p, o, ctxid)
                                for s, p, o
                                in ctx.contents_triples()
                                if not (isinstance(s, Variable) or
                                        isinstance(p, Variable) or
                                        isinstance(o, Variable)))
        for cctx in ctx.imports:
            self._init_store0(cctx, seen)

    def close(self, commit_pending_transaction=False):
        self.ctx = None
        self._memory_store = None

    # RDF APIs
    def add(self, triple, context, quoted=False):
        raise NotImplementedError("This is a query-only store")

    def addN(self, quads):
        raise NotImplementedError("This is a query-only store")

    def remove(self, triple, context=None):
        raise NotImplementedError("This is a query-only store")

    def triples(self, triple_pattern, context=None):
        if self._memory_store is None:
            raise ContextStoreException("Database has not been opened")
        context = getattr(context, 'identifier', context)
        context_triples = []
        if self._store_store is not None:
            context_triples.append(self._store_store.triples(triple_pattern,
                                                             context))
        return chain(self._memory_store.triples(triple_pattern, context),
                     *context_triples)

    def __len__(self, context=None):
        """
        Number of statements in the store. This should only account for non-
        quoted (asserted) statements if the context is not specified,
        otherwise it should return the number of statements in the formula or
        context given.

        :param context: a graph instance to query or None

        """
        if self._memory_store is None:
            raise ContextStoreException("Database has not been opened")
        if self._store_store is None:
            return len(self._memory_store)
        else:
            # We don't know which triples may overlap, so we can't return an accurate count without doing something
            # expensive, so we just give up
            raise NotImplementedError()

    def contexts(self, triple=None):
        """
        Generator over all contexts in the graph. If triple is specified,
        a generator over all contexts the triple is in.

        if store is graph_aware, may also return empty contexts

        :returns: a generator over Nodes
        """
        if self._memory_store is None:
            raise ContextStoreException("Database has not been opened")
        seen = set()
        rest = ()

        if self._store_store is not None:
            rest = self._store_store.contexts(triple)

        for ctx in chain(self._memory_store.contexts(triple), rest):
            if ctx in seen:
                continue
            seen.add(ctx)
            yield ctx


class RDFContextStore(Store):
    # Returns triples imported by the given context
    context_aware = True

    def __init__(self, context=None, imports_graph=None, include_imports=True, **kwargs):
        super(RDFContextStore, self).__init__(**kwargs)
        self.__graph = context.rdf
        self.__imports_graph = imports_graph
        self.__store = self.__graph.store
        self.__context = context
        self.__context_transitive_imports = None
        self.__include_imports = include_imports
        self.__query_perctx = False

    def __init_contexts(self):
        if self.__store is not None and self.__context_transitive_imports is None:
            if not self.__context or self.__context.identifier is None:
                self.__context_transitive_imports = {getattr(x, 'identifier', x)
                                                     for x in self.__store.contexts()}
            elif self.__include_imports:
                imports = transitive_lookup(self.__store,
                                            self.__context.identifier,
                                            CONTEXT_IMPORTS,
                                            self.__imports_graph)
                self.__context_transitive_imports = imports
            else:
                # XXX we should maybe check that the provided context actually exists in
                # the backing graph -- at this point, it's more-or-less assumed in this
                # case though if self.__include_imports is True, we could have an empty
                # set of imports => we query against everything
                self.__context_transitive_imports = set([self.__context.identifier])

            total_triples = self.__store.__len__()
            per_ctx_triples = sum(self.__store.__len__(context=ctx)
                        for ctx in self.__context_transitive_imports)

            self.__query_perctx = total_triples > per_ctx_triples

    def triples(self, pattern, context=None):
        self.__init_contexts()

        ctx = self._determine_context(context)
        if ctx is _BAD_CONTEXT:
            return

        # If the sum of lengths of the selected contexts is less than total number of
        # triples, query each context in series
        if pattern == (None, None, None) and ctx is None and self.__query_perctx:
            imports = self.__context_transitive_imports
            store = self.__store
            for ctx0 in imports:
                for t, tctxs in store.triples(pattern, ctx0):
                    contexts = set(getattr(c, 'identifier', c) for c in tctxs)
                    yield t, imports & contexts
        else:
            for t in self.__store.triples(pattern, ctx):
                contexts = set(getattr(c, 'identifier', c) for c in t[1])
                if self.__context_transitive_imports:
                    inter = self.__context_transitive_imports & contexts
                else:
                    inter = contexts
                if inter:
                    yield t[0], inter

    def remove(self, pattern, context=None):
        self.__init_contexts()

        ctx = self._determine_context(context)
        if ctx is _BAD_CONTEXT:
            return
        for t in self.__store.triples(pattern, ctx):
            triple = t[0]
            contexts = set(getattr(c, 'identifier', c) for c in t[1])
            if self.__context_transitive_imports:
                inter = self.__context_transitive_imports & contexts
            else:
                inter = contexts
            for ctx in inter:
                self.__store.remove((triple[0], triple[1], triple[2]), ctx)

    def triples_choices(self, pattern, context=None):
        self.__init_contexts()

        ctx = self._determine_context(context)
        if ctx is _BAD_CONTEXT:
            return

        for t in self.__store.triples_choices(pattern, ctx):
            contexts = set(getattr(c, 'identifier', c) for c in t[1])
            if self.__context_transitive_imports:
                inter = self.__context_transitive_imports & contexts
            else:
                inter = contexts

            if inter:
                yield t[0], inter

    def _determine_context(self, context):
        context = getattr(context, 'identifier', context)
        if context is not None and context not in self.__context_transitive_imports:
            return _BAD_CONTEXT
        if len(self.__context_transitive_imports) == 1 and context is None:
            # Micro-benchmarked this with timeit it's faster than tuple(s)[0] and
            # next(iter(s),None)
            for context in self.__context_transitive_imports:
                break
        return None if context is None else self.__graph.get_context(context)

    def contexts(self, triple=None):
        if triple is not None:
            for x in self.triples(triple):
                for c in x[1]:
                    yield getattr(c, 'identifier', c)
        else:
            self.__init_contexts()
            for c in self.__context_transitive_imports:
                yield c

    def namespace(self, prefix):
        return self.__store.namespace(prefix)

    def prefix(self, uri):
        return self.__store.prefix(uri)

    def bind(self, prefix, namespace):
        return self.__store.bind(prefix, namespace)

    def namespaces(self):
        for x in self.__store.namespaces():
            yield x


_BAD_CONTEXT = object()
