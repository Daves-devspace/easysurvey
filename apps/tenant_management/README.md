# Tenant Management Module

This module handles all tenant-related operations including billing, payments, and invoice management.

## New Architecture

The system is being refactored to a service-oriented architecture with the following structure:

### Services
- `BillingService`: Handles billing period calculations and invoice retrieval
- `InvoiceService`: Manages invoice-related operations
- `PaymentService`: Handles payment processing using strategy pattern
- `DepositService`: Manages deposit operations

### Payment Strategies
- `NewPaymentStrategy`: Processes new payments from external sources
- `CreditApplicationStrategy`: Applies existing tenant credits to invoices

### Utilities
- `date_utils.py`: Date-related helper functions
- `billing_utils.py`: Billing period calculations
- `payment_utils.py`: Payment-related helper functions
- `logging_utils.py`: Structured logging utilities
- `monitoring_utils.py`: Performance monitoring utilities
- `decorators.py`: Decorators for common functionality

### Signals
Signals are organized by concern:
- `invoice_signals.py`: Signals related to invoice operations
- `payment_signals.py`: Signals related to payment operations
- `meter_signals.py`: Signals related to meter readings

## Phase 3 Changes

1. **Enhanced Logging**: Implemented structured logging throughout the system
2. **Performance Monitoring**: Added performance monitoring decorators to track function execution time and database queries
3. **Comprehensive Testing**: Created a complete test suite covering all services
4. **Database Optimization**: Added strategic indexes for better query performance
5. **Error Handling**: Enhanced error handling with custom exceptions

## Key Features

### Structured Logging
All log messages are now structured JSON objects with consistent fields:
- `timestamp`: ISO format timestamp
- `level`: Log level (INFO, WARNING, ERROR, etc.)
- `message`: Human-readable message
- `module`: Module where the log originated
- Additional context-specific fields

### Performance Monitoring
Key functions are decorated with `@monitor_performance` which tracks:
- Execution time
- Database query count
- Automatic warning for slow functions

### Comprehensive Testing
The test suite includes:
- Unit tests for all services
- Integration tests for payment processing
- Database transaction tests
- Error scenario tests

### Database Optimization
Strategic indexes have been added to:
- Improve query performance for common filters
- Support range queries on date fields
- Optimize join operations

## Usage Examples

### Processing a Payment
```python
from apps.tenant_management.services import PaymentService

result = PaymentService.process_payment(
    tenant=tenant,
    amount=Decimal('15000.00'),
    reference="Rent Payment",
    method="Mpesa"
)