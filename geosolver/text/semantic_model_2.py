import itertools
import numpy as np
from scipy.optimize import minimize
from geosolver.text.dist_utils import log_normalize
from geosolver.text.feature_function import FeatureFunction
from geosolver.text.ontology import function_signatures, issubtype
from geosolver.text.ontology_states import FunctionSignature
from geosolver.text.rule import UnaryRule, SemanticRule, TagRule
from geosolver.text.rule import BinaryRule
from geosolver.text.transitions import semantic_rule_to_tag_rules

__author__ = 'minjoon'

class SemanticModel(object):
    def __init__(self, feature_function, weights=None):
        assert isinstance(feature_function, FeatureFunction)
        self.feature_function = feature_function
        if weights is None:
            weights = np.zeros(feature_function.dim)
        self.weights = weights
        self.impliable_signatures = set()
        self.localities = {}

    def fit(self, rules, reg_const):
        self._add_impliable_signatures(rules)
        # num_vector_list = [self.feature_function.evaluate(rule) for rule in rules]
        denom_rules_list = [self.get_possible_rules(rule.words, rule.syntax_tree, rule.tags,
                                                    rule.parent_index, rule.parent_signature,
                                                    lifted_tag_rules=set(semantic_rule_to_tag_rules(rule)))
                            for rule in rules]
        denom_vectors_list = [[self.feature_function.evaluate(denom_rule) for denom_rule in denom_rules] for denom_rules in denom_rules_list]
        num_index_list = [denom_rules_list[rule_index].index(rule) for rule_index, rule in enumerate(rules)]


        def get_log_distribution(weights, curr_denom_vectors):
            dist = {idx: np.dot(weights, curr_denom_vector) for idx, curr_denom_vector in enumerate(curr_denom_vectors)}
            norm_dist = log_normalize(dist)
            return norm_dist

        def get_log_prob(weights, curr_denom_vectors, num_index):
            dist = get_log_distribution(weights, curr_denom_vectors)
            return dist[num_index]

        def original_function(weights):
            return sum(get_log_prob(weights, denom_vectors_list[curr_rule_index], curr_num_index)
                       for curr_rule_index, curr_num_index in enumerate(num_index_list)) - 0.5*reg_const*np.dot(weights, weights)

        sum_term = np.sum((denom_vectors_list[rule_index][num_index] for rule_index, num_index in enumerate(num_index_list)), 0)
        def grad_function(weights):
            out = sum_term - reg_const*weights
            for curr_rule_index in range(len(rules)):
                curr_denom_vectors = denom_vectors_list[curr_rule_index]
                dist = get_log_distribution(weights, curr_denom_vectors)
                for num_index, logp in dist.iteritems():
                    out -= np.exp(dist[num_index])*curr_denom_vectors[num_index]
            return out

        negated_original = lambda weights: -original_function(weights)
        negated_grad = lambda weights: -grad_function(weights)

        result = minimize(negated_original, self.weights, method='L-BFGS-B', jac=negated_grad) # jac
        self.weights = result.x
        print self.weights

    def _add_impliable_signatures(self, rules):
        for rule in rules:
            if isinstance(rule, UnaryRule):
                if rule.parent_index is None:
                    self.impliable_signatures.add(rule.parent_signature)
                if rule.child_index is None:
                    self.impliable_signatures.add(rule.child_signature)
            elif isinstance(rule, BinaryRule):
                if rule.parent_index is None:
                    self.impliable_signatures.add(rule.parent_signature)
                if rule.a_index is None:
                    self.impliable_signatures.add(rule.a_signature)
                if rule.b_index is None:
                    self.impliable_signatures.add(rule.b_signature)

    def get_log_distribution(self, words, syntax_tree, tags, parent_index, parent_signature,
                             excluding_indices=set(), lifted_tag_rules=set()):
        distribution = {}
        local_rules = self.get_possible_rules(words, syntax_tree, tags, parent_index, parent_signature,
                                              excluding_indices, lifted_tag_rules)
        for rule in local_rules:
            feature_vector = self.feature_function.evaluate(rule)
            numerator = np.dot(self.weights, feature_vector)
            distribution[rule] = numerator

        if len(distribution) == 0:
            return {}

        normalized_distribution = log_normalize(distribution)
        return normalized_distribution

    def get_log_prob(self, rule, excluding_indices=set(), lifted_tag_rules=set()):
        assert isinstance(rule, SemanticRule)
        distribution = self.get_log_distribution(rule.words, rule.syntax_tree, rule.tags,
                                                 rule.parent_index, rule.parent_signature,
                                                 excluding_indices, lifted_tag_rules)
        if rule not in distribution:
            return -9999
        else:
            return distribution[rule]

    def get_possible_rules(self, words, syntax_tree, tags, parent_index, parent_signature, excluding_indices=set(),
                           lifted_tag_rules=set()):
        return []


    def get_next_tag_rules(self, words, syntax_tree, tags, parent_index, parent_signature, excluding_indices=set(), lifted_tag_rules=set()):
        assert isinstance(parent_signature, FunctionSignature)
        if parent_index is None:
            excluding_indices = set([parent_index])
        else:
            excluding_indices = set(excluding_indices)
            excluding_indices.add(parent_index)

        explicit_tag_rules = set(TagRule(words, syntax_tree, index, tags[index])
                                 for index in words.keys() if tags[index] is not None)
        implicit_tag_rules = set(TagRule(words, syntax_tree, None, signature)
                                 for signature in function_signatures.values())
        indexed_tag_rules = explicit_tag_rules | lifted_tag_rules

        if parent_index is not None and tags[parent_index] in self.localities:
            locality = self.localities[tags[parent_index]]
            local_indices = set(range(parent_index-locality, parent_index+locality+1))
        else:
            local_indices = words.keys()

        # filter by excluding indices and locality
        filtered_indexed_tag_rules = set(tag_rule for tag_rule in indexed_tag_rules
                                       if tag_rule.index not in excluding_indices and tag_rule.index in local_indices)
        filtered_implicit_tag_rules = set(tag_rule for tag_rule in implicit_tag_rules
                                          if tag_rule.signature in self.impliable_signatures)

        return filtered_indexed_tag_rules | filtered_implicit_tag_rules


class UnarySemanticModel(SemanticModel):
    def get_possible_rules(self, words, syntax_tree, tags, parent_index, parent_signature, excluding_indices=set(),
                           lifted_tag_rules=set()):

        assert isinstance(parent_signature, FunctionSignature)
        assert parent_signature.is_unary()

        next_tag_rules = self.get_next_tag_rules(words, syntax_tree, tags, parent_index, parent_signature,
                                                 excluding_indices, lifted_tag_rules)

        unary_rules = []
        for tag_rule in next_tag_rules:
            if issubtype(tag_rule.signature.return_type, parent_signature.arg_types[0]):
                unary_rule = UnaryRule(words, syntax_tree, tags, parent_index, parent_signature, tag_rule.index, tag_rule.signature)
                unary_rules.append(unary_rule)

        return unary_rules


class BinarySemanticModel(SemanticModel):
    def get_possible_rules(self, words, syntax_tree, tags, parent_index, parent_signature, excluding_indices=set(),
                           lifted_tag_rules=set()):
        assert isinstance(parent_signature, FunctionSignature)
        assert parent_signature.is_binary()

        next_tag_rules = self.get_next_tag_rules(words, syntax_tree, tags, parent_index, parent_signature,
                                                 excluding_indices, lifted_tag_rules)

        binary_rules = []
        for a_tag, b_tag in itertools.product(next_tag_rules, repeat=2):
            if issubtype(a_tag.signature.return_type, parent_signature.arg_types[0]) and \
                    issubtype(b_tag.signature.return_type, parent_signature.arg_types[1]):
                binary_rule = BinaryRule(words, syntax_tree, tags, parent_index, parent_signature,
                                         a_tag.index, a_tag.signature, b_tag.index, b_tag.signature)
                binary_rules.append(binary_rule)

        return binary_rules
