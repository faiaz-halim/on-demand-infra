import pytest
from security_utils import sanitize_terraform_input, sanitize_kubernetes_input

def test_sanitize_terraform_input_invalid():
    input_str = "test!@#string"
    assert sanitize_terraform_input(input_str) is None

def test_sanitize_kubernetes_input_invalid():
    input_str = "test!@#string"
    assert sanitize_kubernetes_input(input_str) is None

def test_sanitize_terraform_input_valid():
    input_str = "test-string_with.dot"
    expected = "test-string_with.dot"
    assert sanitize_terraform_input(input_str) == expected

def test_sanitize_kubernetes_input_valid():
    input_str = "test-string-with.dot"
    expected = "test-string-with.dot"
    assert sanitize_kubernetes_input(input_str) == expected

def test_sanitize_terraform_input_empty():
    input_str = ""
    expected = ""
    assert sanitize_terraform_input(input_str) == expected

def test_sanitize_kubernetes_input_empty():
    input_str = ""
    expected = ""
    assert sanitize_kubernetes_input(input_str) == expected
