import sadi
import rdflib
import setlr
from datetime import datetime
from nanopub import Nanopublication
import flask_ld as ld
import flask

graphene = rdflib.Namespace('http://vocab.rpi.edu/graphene/')
np = rdflib.Namespace("http://www.nanopub.org/nschema#")
prov = rdflib.Namespace("http://www.w3.org/ns/prov#")
dc = rdflib.Namespace("http://purl.org/dc/terms/")
sio = rdflib.Namespace("http://semanticscience.org/resource/")
setl = rdflib.Namespace("http://purl.org/twc/vocab/setl/")
pv = rdflib.Namespace("http://purl.org/net/provenance/ns#")

class Service(sadi.Service):

    dry_run = False
    
    activity_class = graphene.Agent
    
    def get_query(self):
        return '''select ?resource where {
    ?resource rdf:type/rdfs:subClassOf* %s.
    filter not exists { ?resource rdf:type/rdfs:subClassOf* %s. }
}''' % (self.getInputClass().n3(), self.getOutputClass().n3())
    
    def annotateServiceDescription(self, desc):
        desc.add(self.query_predicate, rdflib.Literal(self.get_query()))

    def processGraph(self,content, type):
        inputGraph = sadi.SADIGraph()
        self.deserialize(inputGraph, content, type)
        return process

    def explain(self, nanopub, i, o):
        activity = nanopub.provenance.resource(rdflib.BNode())
        activity.add(rdflib.RDF.type, self.activity_class)
        nanopub.pubinfo.add((o.identifier, rdflib.RDF.type, self.getOutputClass()))
        nanopub.provenance.add((nanopub.assertion.identifier, prov.wasGeneratedBy, activity.identifier))

    def getInstances(self, graph):
        return [graph.resource(i) for i, in graph.query(self.get_query())]
    
    def process_graph(self, inputGraph):
        instances = self.getInstances(inputGraph)
        results = []
        for i in instances:
            print "Processing", i.identifier, self
            output_nanopub = Nanopublication()
            o = output_nanopub.assertion.resource(i.identifier) # OutputClass(i.identifier)
            result = self.process_nanopub(i, o, output_nanopub)
            print "Output Graph", output_nanopub.identifier, len(output_nanopub)
            for new_np in self.app.nanopub_manager.prepare(rdflib.ConjunctiveGraph(store=output_nanopub.store)):
                if len(new_np.assertion) == 0:
                    continue
                self.explain(new_np, i, o)
                new_np.add((new_np.identifier, sio.isAbout, i.identifier))
                #print new_np.serialize(format="trig")
                if not self.dry_run:
                    self.app.nanopub_manager.publish(new_np)
                results.append(new_np)
        return results

    def process_nanopub(self, i, o, output_nanopub):
        self.process(i, o)
    
class UpdateChangeService(Service):
    @property
    def query_predicate(self):
        return graphene.updateChangeQuery
    
    def explain(self, nanopub, i, o):
        Service.explain(self, nanopub, i, o)
        np_assertions = list(i.graph.subjects(rdflib.RDF.type, np.Assertion))
        for assertion in np_assertions:
            nanopub.provenance.add((activity.identifier, prov.used, assertion))
            nanopub.provenance.add((nanopub.assertion.identifier, prov.wasDerivedFrom, assertion))
            nanopub.pubinfo.add((nanopub.assertion.identifier, dc.created, rdflib.Literal(datetime.utcnow())))

class GlobalChangeService(Service):
    @property
    def query_predicate(self):
        return graphene.globalChangeQuery

class Crawler(UpdateChangeService):

    activity_class = graphene.GraphCrawl

    def __init__(self, depth=-1, predicates=[None]):
        self.depth = depth
        self.predicates = predicates
    
    def getInputClass(self):
        return graphene.CrawlerStart

    def getOutputClass(self):
        return graphene.Crawled
    
    def get_query(self):
        return '''select ?resource where {
    ?resource rdf:type/rdfs:subClassOf* %s.
}''' % self.getInputClass().n3()

    def process(self, i, o):
        cache = set()
        todo = [(i.identifier, self.depth)]
        # this non-recursive form does a BFS of the linked data graph.
        while len(todo) > 0:
            uri, depth = todo.pop()
            #print uri, depth, len(todo)
            if uri in cache:
                continue
            node = None
            node = self.app.get_resource(uri)
            cache.add(uri)
            if depth != 0:
                for p in self.predicates:
                    todo.extend([(x.identifier, depth-1) for x in node[p]])

class OntologyImporter(GlobalChangeService):

    activity_class = graphene.OntologyImport
        
    def getInputClass(self):
        return OWL.Ontology

    def getOutputClass(self):
        return graphene.ImportedOntology

    def process(self, i, o):
        pass

class SETLMaker(GlobalChangeService):
    activity_class = setl.Planner

    def getInputClass(self):
        return pv.File

    def getOutputClass(self):
        return setl.SETLedFile

    def get_query(self):
        return '''
prefix setl: <http://purl.org/twc/vocab/setl/>
prefix owl:  <''' + rdflib.OWL + '''>
prefix prov: <''' + prov + '''>
select distinct ?resource where {
    ?resource rdf:type/rdfs:subClassOf* ?parameterized_type.
    ?setl_script rdfs:subClassOf setl:SemanticETLScript;
        rdfs:subClassOf*/owl:equivalentClass* [ a owl:Restriction;
            owl:onProperty prov:used;
            owl:someValuesFrom ?parameterized_type
    ].
    filter not exists {
        ?setl_run a ?setl_script;
            prov:used ?resource.
    }
  filter (!regex(str(?resource), "^bnode:"))
}'''

    def process_nanopub(self, i, o, new_np):
        print i.identifier
        for script, np, parameterized_type, p, type_assertion in self.app.db.query('''
prefix setl: <http://purl.org/twc/vocab/setl/>
select distinct ?setl_script ?np ?parameterized_type ?p ?type_assertion where {
    graph ?type_assertion { ?resource rdf:type/rdfs:subClassOf* ?parameterized_type. }
    ?type_np a np:Nanopublication; np:hasAssertion ?type_assertion.
    graph ?assertion {
      ?setl_script rdfs:subClassOf setl:SemanticETLScript;
        rdfs:subClassOf*/owl:equivalentClass* [ a owl:Restriction;
            owl:onProperty ?p;
            owl:someValuesFrom ?parameterized_type
      ].
    }
    ?np a np:Nanopublication; np:hasAssertion ?assertion.
    filter not exists {
        ?setl_run a ?setl_script;
            prov:used ?resource.
    }
}''', initBindings=dict(resource=i.identifier), initNs=self.app.NS.prefixes):
            nanopub = self.app.nanopub_manager.get(np)
            print "Template NP", nanopub.identifier, len(nanopub)
            template_prefix = nanopub.assertion.value(script, setl.hasTemplatePrefix)
            replacement_prefix = self.app.NS.local['setl/'+ld.create_id()+"/"]

            mappings = {}
            for x, in nanopub.assertion.query("select ?x where {?x a ?t}",
                                              initBindings={'t':parameterized_type},
                                              initNs=self.app.NS.prefixes):
                mappings[x] = i.identifier
            
            script_run = rdflib.URIRef(script.replace(template_prefix, replacement_prefix, 1))
            for x, in nanopub.assertion.query("select ?x where {?x a ?t}",
                                              initBindings={'t':script},
                                              initNs=self.app.NS.prefixes):
                mappings[x] = script_run
            def replace(x):
                if x in mappings:
                    return mappings[x]
                if isinstance(x, rdflib.URIRef) and x.startswith("bnode:"):
                    return rdflib.BNode(x.replace("bnode:","",1))
                if isinstance(x, rdflib.URIRef) and x == script:
                    return script
                if isinstance(x, rdflib.URIRef) and x.startswith(template_prefix):
                    return rdflib.URIRef(x.replace(template_prefix, replacement_prefix, 1))
                return x
            for s, p, o in nanopub.assertion:
                new_np.assertion.add((replace(s), replace(p), replace(o)))
            new_np.assertion.add((script_run, rdflib.RDF.type, script))
            new_np.assertion.add((script_run, rdflib.RDF.type, setl.SemanticETLScript))
            new_np.provenance.add((new_np.assertion.identifier, prov.wasDerivedFrom, nanopub.assertion.identifier))
            new_np.provenance.add((new_np.assertion.identifier, prov.wasDerivedFrom, type_assertion))
            print "Instance NP", new_np.identifier, len(new_np)
#            print new_np.serialize(format="trig")
                        
def _satoru_content_handler(location):
    resource = flask.current_app.get_resource(location)
    fileid = resource.value(flask.current_app.NS.graphene.hasFileID)
    if fileid is not None:
        return flask.current_app.file_depot.get(fileid)

setlr.content_handlers.insert(0,_satoru_content_handler)

class SETLr(UpdateChangeService):

    activity_class = setl.SemanticETL
    

    def __init__(self, depth=-1, predicates=[None]):
        self.depth = depth
        self.predicates = predicates
    
    def getInputClass(self):
        return setl.SemanticETLScript

    def getOutputClass(self):
        return graphene.ProcessedSemanticETLScript
    
    def get_query(self):
        return '''select distinct ?resource where { ?resource a %s.}''' % self.getInputClass().n3()
    
    def explain(self, nanopub, i, o):
        np_assertions = list(i.graph.subjects(rdflib.RDF.type, np.Assertion)) + [nanopub.assertion.identifier]
        activity = nanopub.provenance.resource(rdflib.BNode())
        activity.add(rdflib.RDF.type, i.identifier)
        nanopub.provenance.add((nanopub.assertion.identifier, prov.wasGeneratedBy, activity.identifier))
        for assertion in np_assertions:
            nanopub.provenance.add((activity.identifier, prov.used, assertion))
            nanopub.provenance.add((nanopub.assertion.identifier, prov.wasDerivedFrom, assertion))
            nanopub.pubinfo.add((nanopub.assertion.identifier, prov.wasAttributedTo, i.identifier))
            nanopub.pubinfo.add((nanopub.assertion.identifier, prov.wasAttributedTo, i.identifier))

    def process(self, i, o):
        setl_graph = i.graph
        resources = setlr._setl(setl_graph)
        # retire old copies
        old_np_map = {}
        for new_np, assertion, orig in  self.app.db.query('''select distinct ?np ?assertion ?original_uri where {
    ?np np:hasAssertion ?assertion.
    ?assertion a np:Assertion;
        prov:wasGeneratedBy/a ?setl;
        prov:wasQuotedFrom ?original_uri.
}''', initBindings=dict(setl=i.identifier), initNs=dict(prov=prov, np=np)):
            old_np_map[orig] = assertion
            self.app.nanopub_manager.retire(new_np)
            #print resources
        for output_graph in setl_graph.subjects(prov.wasGeneratedBy, i.identifier):
            out = resources[output_graph]
            out_conjunctive = rdflib.ConjunctiveGraph(store=out.store, identifier=output_graph)
            #print "Generated graph", out.identifier, len(out), len(out_conjunctive)
            mappings = {}
            for new_np in self.app.nanopub_manager.prepare(out_conjunctive, mappings=mappings):
                self.explain(new_np, i, o)
                print "Publishing", new_np.identifier
                orig = [orig for orig, new in mappings.items() if new == new_np.assertion.identifier]
                if len(orig) == 0:
                    continue
                orig = orig[0]
                print orig
                if isinstance(orig, rdflib.URIRef):
                    new_np.pubinfo.add((new_np.assertion.identifier, prov.wasQuotedFrom, orig))
                    if orig in old_np_map:
                        new_np.pubinfo.add((new_np.assertion.identifier, prov.wasRevisionOf, old_np_map[orig]))
                print "Nanopub assertion has", len(new_np.assertion), "statements."
                self.app.nanopub_manager.publish(new_np)
                print 'Published'
                
