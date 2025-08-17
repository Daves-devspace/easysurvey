# Survey Agency Management System

A comprehensive Django-based management platform designed specifically for survey agencies to streamline operations, automate workflows, and centralize business processes.

## 🏢 Overview

This system manages the complete lifecycle of survey agency operations including client management, service delivery, payments, document storage, staff management, and automated communications. Built with Django and designed for scalability and ease of use.

## ✨ Key Features

### 📋 Client Management
- **Complete client lifecycle management** with detailed profiles
- **Service tracking** from request through completion
- **Multi-tab interface**: Profile, Documents, Accounts, Messages
- **Real-time status updates** and progress tracking

### 🏗️ Service Workflows
- **Title Deed Services**: Multi-step processes with automated progression
- **Ground Services**: One-off scheduled visits with booking management
- **Process automation** with SMS notifications at each step
- **Payment gating** to control workflow progression

### 💰 Financial Management
- **Comprehensive payment tracking** with allocation to processes/subservices
- **Receipt generation** and printing capabilities
- **Expense management** with categorization
- **Revenue analytics** with visual charts and reports
- **Legal partner payout** management with safeguards

### 📱 Communication Hub
- **Automated SMS notifications** for process updates
- **Broadcast messaging** with personalization placeholders
- **Message scheduling** and delivery tracking
- **Failed message retry** capabilities
- **Comprehensive message logs**

### 📅 Booking & Scheduling
- **Automated booking creation** for ground services
- **Calendar interface** with day/week/month views
- **Surveyor assignment** and tracking
- **Same-day reminder automation**
- **Booking status management**

### 📁 Document Management
- **Client-specific document storage**
- **Office document centralization**
- **Multi-format file support** (PDF, JPG, PNG, DOC)
- **Secure document sharing** via WhatsApp/Email
- **Document categorization** and search

### 👥 HR & Payroll
- **Employee management** with role-based access
- **Comprehensive payroll system** with allowances/deductions
- **Automated payroll generation**
- **Payment tracking** and history
- **Role-based permissions**

## 🏗️ System Architecture

### Core Modules

1. **Clients** - Central client management with service tracking
2. **Bookings** - Scheduling and surveyor management
3. **Office Documents** - Company-wide document storage
4. **Communication** - Messaging and notification hub
5. **Accounts** - Financial management and analytics
6. **Employees** - HR and payroll management

### Technology Stack
- **Backend**: Django (Python)
- **Database**: PostgreSQL/MySQL/SQLite
- **Frontend**: Bootstrap, JavaScript
- **Messaging**: SMS API integration
- **File Storage**: Local/Cloud storage support
- **Task Queue**: Background job processing

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Django 4.x
- Database (PostgreSQL recommended)
- SMS service provider API credentials

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Daves-devspace/easysurvey.git
   cd easysurvey
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment setup**
   ```bash
   cp .env.example .env
   # Edit .env with your database and SMS API credentials
   ```

5. **Database setup**
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   python manage.py createsuperuser
   ```

6. **Run the server**
   ```bash
   python manage.py runserver
   ```

Visit `http://localhost:8000` to access the system.

## 📖 User Guide

### For Administrators
- **Client Management**: Add clients, manage services, track progress
- **Financial Oversight**: Monitor payments, expenses, and revenue
- **Staff Management**: Handle employee records and payroll
- **System Configuration**: Manage settings and user permissions

### For Staff Members
- **Service Processing**: Complete processes, update statuses
- **Document Handling**: Upload, organize, and share client documents
- **Communication**: Send messages and track client interactions
- **Booking Management**: Assign surveyors and manage schedules

### For Surveyors
- **Booking View**: Access assigned ground service appointments
- **Status Updates**: Mark bookings as handled after completion
- **Client Communication**: Access client contact information

## 🔄 Key Workflows

### Title Deed Service Flow
1. Add title deed service to client → System sends "Process 1 started" SMS
2. Complete Process 1 → System moves to Process 2 → Sends notification
3. Repeat until all processes complete → Final notification sent

### Ground Service Flow
1. Add ground service with scheduled date/time → Booking auto-created
2. Assign surveyor → System sends scheduling message
3. Same-day reminder sent automatically
4. Mark booking handled after completion

### Payment Processing
1. Record client payment → Allocate to specific processes/subservices
2. System updates balances → Generates receipt
3. Payment gating enforces workflow if configured

## 🎯 Business Rules

- **Sequential Processing**: Title deed processes must complete in order
- **Payment Gating**: Optional rule to require payment before process advancement
- **Legal Payout Protection**: Agency only pays legal partners after client pays fully
- **Payroll Enforcement**: All salaries must be paid before generating next month's payroll
- **Automated Notifications**: SMS sent automatically for process updates and scheduling

## 📊 Analytics & Reporting

- **Revenue Analytics**: Track gross/net revenue over time
- **Service Performance**: Monitor which services generate most revenue
- **Payment Status**: Track fully paid vs partially paid services
- **Staff Performance**: Monitor surveyor assignments and completions
- **Message Analytics**: Track delivery rates and communication effectiveness

## 🔧 Configuration

### SMS Integration
Configure your SMS provider credentials in settings:
```python
SMS_API_KEY = 'your-api-key'
SMS_API_URL = 'your-provider-url'
```

### File Storage
Set up file storage for documents:
```python
MEDIA_ROOT = '/path/to/media/files'
MEDIA_URL = '/media/'
```

### Background Tasks
Configure Celery for automated reminders:
```python
CELERY_BROKER_URL = 'redis://localhost:6379'
CELERY_RESULT_BACKEND = 'redis://localhost:6379'
```

## 🤖 Built-in Chatbot

The system includes an AI-powered chatbot assistant that can help users with:
- System navigation and feature explanations
- Workflow guidance and best practices
- Troubleshooting common issues
- Feature discovery and usage tips

The chatbot uses a comprehensive knowledge base covering all system modules and can be easily updated by modifying `static/assets/json/knowledgeBase.json`.

## 🔒 Security Features

- **Role-based access control** with granular permissions
- **Secure document storage** with access logging
- **Session management** with timeout controls
- **Data validation** and sanitization
- **Audit trails** for critical operations

## 📱 Mobile Support

The system is responsive and works on mobile devices, with optimized interfaces for:
- Client information lookup
- Payment recording
- Document access
- Booking management
- Communication features

## 🛠️ Development

### Project Structure
```
survey-management-system/
├── apps/
│   ├── EasyDocs/          # Main application
│   ├── Employee/          # HR & Payroll
│   └── tenant_management/ # Multi-tenancy
├── static/                # CSS, JS, Images
├── templates/             # HTML templates
├── media/                 # Uploaded files
└── requirements.txt       # Dependencies
```

### Contributing
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

### Running Tests
```bash
python manage.py test
```

## 📞 Support

- **Documentation**: Check the in-system help and chatbot
- **Issues**: Report bugs via GitHub issues
- **Feature Requests**: Submit enhancement requests
- **Community**: Join our discussions

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with Django framework
- Uses Bootstrap for responsive design
- Integrated SMS services for notifications
- Powered by modern web technologies

---

**Made with ❤️ for Survey Agencies**

For more information, visit our [documentation](link-to-docs) or try the [live demo](link-to-demo).