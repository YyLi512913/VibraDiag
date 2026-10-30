"""Select new verification receipts without overwriting historical stage results."""
import argparse


def verification_options(default_receipt,default_tests):
    parser=argparse.ArgumentParser(description='Isolated synthetic HTTP verification; no factory data or model calls')
    parser.add_argument('--receipt',default=default_receipt,help='project-relative output receipt path')
    parser.add_argument('--tests-report',default=default_tests,help='project-relative successful JUnit report')
    args=parser.parse_args()
    return {'receipt_path':args.receipt,'tests_report':args.tests_report}
