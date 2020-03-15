from io import StringIO
import tempfile
from os.path import join as p
from os import makedirs, chmod
from unittest.mock import patch, Mock

import pytest
from rdflib.term import URIRef
from rdflib.graph import ConjunctiveGraph


from owmeta_core.bundle import (Remote, URLConfig, HTTPBundleLoader, Bundle, BundleNotFound,
                           Descriptor, DependencyDescriptor)
from owmeta_core.agg_store import UnsupportedAggregateOperation


def test_write_read_remote_1():
    out = StringIO()
    r0 = Remote('remote')
    r0.write(out)
    out.seek(0)
    r1 = Remote.read(out)
    assert r0 == r1


def test_write_read_remote_2():
    out = StringIO()
    r0 = Remote('remote')
    r0.add_config(URLConfig('http://example.org/bundle_remote0'))
    r0.add_config(URLConfig('http://example.org/bundle_remote1'))
    r0.write(out)
    out.seek(0)
    r1 = Remote.read(out)
    assert r0 == r1


def test_get_http_url_loaders():
    '''
    Find loaders for HTTP URLs
    '''
    out = StringIO()
    r0 = Remote('remote')
    r0.add_config(URLConfig('http://example.org/bundle_remote0'))
    for l in r0.generate_loaders():
        if isinstance(l, HTTPBundleLoader):
            return

    raise AssertionError('No HTTPBundleLoader was created')


def test_remote_generate_uploaders_skip():
    mock = Mock()
    with patch('owmeta_core.bundle.UPLOADER_CLASSES', [mock]):
        out = StringIO()
        r0 = Remote('remote')
        r0.add_config(URLConfig('http://example.org/bundle_remote0'))
        for ul in r0.generate_uploaders():
            pass
    mock.can_upload_to.assert_called()


def test_remote_generate_uploaders_no_skip():
    mock = Mock()
    mock.can_upload_to.return_value = True
    ac = URLConfig('http://example.org/bundle_remote0')
    with patch('owmeta_core.bundle.UPLOADER_CLASSES', [mock]):
        out = StringIO()
        r0 = Remote('remote')
        r0.add_config(ac)
        loader = None
        for ul in r0.generate_uploaders():
            pass
    mock.assert_called_with(ac)


def test_latest_bundle_fetched():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        makedirs(p(bundles_directory, 'example', '1'))
        makedirs(p(bundles_directory, 'example', '2'))
        expected = p(bundles_directory, 'example', '3')
        makedirs(expected)
        b = Bundle('example', bundles_directory=bundles_directory)
        assert expected == b._get_bundle_directory()


def test_specified_version_fetched():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        makedirs(p(bundles_directory, 'example', '1'))
        expected = p(bundles_directory, 'example', '2')
        makedirs(expected)
        makedirs(p(bundles_directory, 'example', '3'))
        b = Bundle('example', version=2, bundles_directory=bundles_directory)
        assert expected == b._get_bundle_directory()


def test_no_versioned_bundles():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        makedirs(p(bundles_directory, 'example'))
        b = Bundle('example', bundles_directory=bundles_directory)
        with pytest.raises(BundleNotFound, match='No versioned bundle directories'):
            b._get_bundle_directory()


def test_specified_bundle_does_not_exist():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        makedirs(p(bundles_directory, 'example'))
        b = Bundle('example', bundles_directory=bundles_directory, version=2)
        with pytest.raises(BundleNotFound, match='at version 2.*specified version'):
            b._get_bundle_directory()


def test_specified_bundle_directory_does_not_exist():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        makedirs(bundles_directory)
        b = Bundle('example', bundles_directory=bundles_directory)
        with pytest.raises(BundleNotFound, match='Bundle directory'):
            b._get_bundle_directory()


def test_specified_bundles_root_directory_does_not_exist():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        b = Bundle('example', bundles_directory=bundles_directory)
        with pytest.raises(BundleNotFound, match='Bundle directory'):
            b._get_bundle_directory()


def test_specified_bundles_root_permission_denied():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        b = Bundle('example', bundles_directory=bundles_directory)
        makedirs(bundles_directory)
        chmod(bundles_directory, 0)
        try:
            with pytest.raises(OSError, match='[Pp]ermission denied'):
                b._get_bundle_directory()
        finally:
            chmod(bundles_directory, 0o777)


def test_ignore_non_version_number():
    with tempfile.TemporaryDirectory(prefix=__name__ + '.') as tempdir:
        bundles_directory = p(tempdir, 'bundles')
        b = Bundle('example', bundles_directory=bundles_directory)
        makedirs(p(bundles_directory, 'example', 'ignore_me'))
        expected = p(bundles_directory, 'example', '5')
        makedirs(expected)
        actual = b._get_bundle_directory()
        assert actual == expected


def test_descriptor_dependency():
    d = Descriptor.make({
        'id': 'testBundle',
        'dependencies': [
            'dep1',
            {'id': 'dep2', 'version': 2},
            ('dep3', 4),
            ('dep4',)
        ]
    })
    assert DependencyDescriptor('dep1') in d.dependencies
    assert DependencyDescriptor('dep2', 2) in d.dependencies
    assert DependencyDescriptor('dep3', 4) in d.dependencies
    assert DependencyDescriptor('dep4') in d.dependencies


def test_triple_in_dependency(custom_bundle):
    '''
    '''
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    trip = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'))
    ctx_graph.add(trip)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert trip in bnd.rdf


def test_quad_in_dependency(custom_bundle):
    '''
    '''
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert quad in bnd.rdf


def test_quad_not_in_dependency(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/other_ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert quad not in bnd.rdf


def test_quad_not_in_dependency(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/other_ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        assert quad not in bnd.rdf


def test_triples_choices(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')])):
            match = True
            break
        assert match


def test_triples_choices_context(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')]),
                context=ctx_graph):
            match = True
            break
        assert match


def test_triples_choices_context_not_included(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctxg
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        match = False
        for x in bnd.rdf.triples_choices(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 [URIRef('http://example.org/obj')]),
                context=ctx_graph):
            match = True
        assert not match


def test_add_to_graph_not_supported(custom_bundle):
    dep_desc = Descriptor.load('''
    id: dep
    includes:
      - http://example.com/ctx
    ''')

    test_desc = Descriptor.load('''
    id: test
    dependencies:
      - dep
    ''')

    depgraph = ConjunctiveGraph()
    ctx_graph = depgraph.get_context('http://example.com/ctx')
    quad = (URIRef('http://example.org/sub'), URIRef('http://example.org/prop'), URIRef('http://example.org/obj'),
            ctx_graph)
    depgraph.add(quad)

    with custom_bundle(dep_desc, graph=depgraph) as depbun, \
            custom_bundle(test_desc, bundles_directory=depbun.bundles_directory) as testbun, \
            Bundle('test', bundles_directory=testbun.bundles_directory) as bnd:
        with pytest.raises(UnsupportedAggregateOperation):
            bnd.rdf.add(
                (URIRef('http://example.org/sub'),
                 URIRef('http://example.org/prop'),
                 URIRef('http://example.org/obj')))
