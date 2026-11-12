#!/usr/bin/env python
"""Teacher's Django management entry point, with portable project settings."""
import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
if __name__ == '__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
