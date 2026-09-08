import unittest

from recipe_box.request_logging import sanitize_request_target


class RequestLoggingTests(unittest.TestCase):
    def test_sensitive_reset_token_is_redacted(self):
        result = sanitize_request_target("/reset-password?token=SECRET&next=%2Fhome")
        self.assertNotIn("SECRET", result)
        self.assertIn("token=%3Credacted%3E", result)
        self.assertIn("next=%2Fhome", result)

    def test_sensitive_code_and_password_values_are_redacted(self):
        result = sanitize_request_target("/reset-password?code=123456&new_password=secret")
        self.assertNotIn("123456", result)
        self.assertNotIn("secret", result)
        self.assertEqual(result, "/reset-password?code=%3Credacted%3E&new_password=%3Credacted%3E")

    def test_ordinary_query_parameters_are_preserved(self):
        self.assertEqual(sanitize_request_target("/recipes?page=2&filter=vegetarian"), "/recipes?page=2&filter=vegetarian")

    def test_no_query_is_unchanged(self):
        self.assertEqual(sanitize_request_target("/health"), "/health")


if __name__ == "__main__":
    unittest.main()
