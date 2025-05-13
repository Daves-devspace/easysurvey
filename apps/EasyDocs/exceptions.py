# exceptions.py
class ClientServiceError(Exception): pass

class BookingError(ClientServiceError): pass

class OverrideError(ClientServiceError): pass
