"""Explicit deliverable destinations and parameter types; same task answers.

This repairs ambiguities observed in development project histories. The
original v1 specifications, traces and failures are preserved unchanged.
"""
from .continuous_projects_v1 import projects as original_projects,initialize,api_payloads


def projects():
    result=original_projects()
    for project in result:
        for index,spec in enumerate(project['turns']):
            spec['request']='Target deliverable: '+spec['artifact']+'. '+spec['request']
            if project['id']=='continuous_inventory' and index==5:
                spec['request']+=' Parameter type: deliveries is a list of dictionaries, each with keys sku (string) and qty (integer); it is not a SKU-to-quantity dictionary.'
            elif project['id']=='continuous_inventory' and index==6:
                spec['request']+=' Parameter type: order is a dictionary mapping SKU strings to integer quantities.'
        project['specification_version']=2
    return result
