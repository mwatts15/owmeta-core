from rdflib import plugin
from rdflib.store import Store, NO_STORE

from .utils import FCN


class AggregateStore(Store):
    '''
    A read-only aggregate of RDFLib `stores <rdflib.store.Store>`
    '''

    context_aware = True
    '''
    Specified by RDFLib. Required to be True for `~rdflib.graph.ConjunctiveGraph` stores.

    Aggregated stores MUST be context-aware. This is enforced by :meth:`open`.
    '''

    # Unlike "awareness" attributes, checking for support of range queries is handled
    # after the store is open, so we don't care if we need to change it to `True` later.
    supports_range_queries = False

    def __init__(self, configuration=None, identifier=None):
        super(AggregateStore, self).__init__(configuration, identifier)
        self.__stores = []
        self.__bound_ns = dict()

    @property
    def stores(self):
        return list(self.__stores)

    # -- Store methods -- #

    def open(self, configuration, create=True):
        '''
        Creates and opens all of the stores specified in the configuration

        Also checks for all aggregated stores to be `context_aware`
        '''
        if not isinstance(configuration, (tuple, list)):
            return NO_STORE
        self.__stores = []
        for store_key, store_conf in configuration:
            store = plugin.get(store_key, Store)()
            store.open(store_conf)
            self.__stores.append(store)
        assert all(x.context_aware for x in self.__stores), ('All aggregated stores must be'
                                                             ' context_aware')
        self.supports_range_queries = all(getattr(x, 'supports_range_queries', False) for x in self.__stores)

    def triples(self, triple, context=None):
        for store in self.__stores:
            for trip in store.triples(triple, context=context):
                yield trip

    def triples_choices(self, triple, context=None):
        for store in self.__stores:
            for trip in store.triples_choices(triple, context=context):
                yield trip

    def __len__(self, context=None):
        # rdflib specifies a context argument for __len__, but how do you even pass that
        # argument to len?
        return sum(len(store) for store in self.__stores)

    def contexts(self, triple=None):
        for store in self.__stores:
            for ctx in store.contexts(triple):
                yield ctx

    def prefix(self, namespace):
        prefix = None
        for store in self.__stores:
            aprefix = store.prefix(namespace)
            if aprefix and prefix and aprefix != prefix:
                msg = 'multiple prefixes ({},{}) for namespace {}'.format(prefix, aprefix, namespace)
                raise AggregatedStoresConflict(msg)
            prefix = aprefix
        return prefix

    def namespace(self, prefix):
        namespace = None
        for store in self.__stores:
            anamespace = store.namespace(prefix)
            if anamespace and namespace and anamespace != namespace:
                msg = 'multiple namespaces ({},{}) for prefix {}'.format(namespace, anamespace, prefix)
                raise AggregatedStoresConflict(msg)
            namespace = anamespace
        if namespace is None:
            namespace = self.__bound_ns.get(prefix)
        return namespace

    def namespaces(self):
        for store in self.__stores:
            for ns in store.namespaces():
                yield ns
        for ns in self.__bound_ns.items():
            yield ns

    def bind(self, prefix, namespace):
        self.__bound_ns[prefix] = namespace

    def close(self, *args, **kwargs):
        for store in self.__stores:
            store.close(*args, **kwargs)

    def gc(self):
        for store in self.__stores:
            store.gc()

    def add(self, *args, **kwargs): raise UnsupportedAggregateOperation

    def addN(self, *args, **kwargs):
        return self.__stores[0].addN(*args, **kwargs)

    def remove(self, *args, **kwargs): raise UnsupportedAggregateOperation
    def add_graph(self, *args, **kwargs): raise UnsupportedAggregateOperation
    def remove_graph(self, *args, **kwargs): raise UnsupportedAggregateOperation
    def create(self, *args, **kwargs): raise UnsupportedAggregateOperation
    def destroy(self, *args, **kwargs): raise UnsupportedAggregateOperation
    def rollback(self, *args, **kwargs): raise UnsupportedAggregateOperation

    def commit(self, *args, **kwargs):
        return self.__stores[0].commit(*args, **kwargs)

    def __repr__(self):
        return '%s(%s)' % (FCN(type(self)), ', '.join(repr(s) for s in self.__stores))


class UnsupportedAggregateOperation(Exception):
    '''
    Thrown for operations which modify a graph and hence are inappropriate for
    `AggregateStore`
    '''


class AggregatedStoresConflict(Exception):
    pass
