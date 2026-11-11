"""The service layer.

Services hold every rule and every piece of logic, and are callable without
HTTP. Views authenticate, deserialise, call one service, serialise and return;
models hold structure and database constraints. That three-layer rule is
absolute.
"""
