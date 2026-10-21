// SPDX-License-Identifier: MIT
pragma solidity ^0.8.36;

/**
 * @title HealthRecords
 * @notice Patient-controlled healthcare record access using Ethereum.
 *
 * IMPORTANT:
 * - Medical files must remain encrypted and off-chain.
 * - Only hashes, permissions and audit events are stored on-chain.
 * - The off-chain server must call hasAccess() before releasing a file
 *   or its decryption key.
 */
contract HealthRecords {
    uint64 public constant EMERGENCY_ACCESS_DURATION = 24 hours;
    uint256 public constant MAX_EMERGENCY_REASON_LENGTH = 280;

    enum AccessType {
        Standard,
        Emergency
    }

    enum AccessStatus {
        Pending,
        Granted,
        Revoked,
        Rejected,
        Expired
    }

    struct MedicalRecord {
        uint256 id;
        address patient;
        bytes32 fileHash;
        bytes32 metadataHash;
        uint64 createdAt;
    }

    struct AccessRecord {
        uint256 id;
        address patient;
        address doctor;
        AccessType accessType;
        AccessStatus status;
        uint64 requestedAt;
        uint64 grantedAt;
        uint64 expiresAt;
        bool flaggedForReview;
        bytes32 reasonHash;
    }

    address public admin;
    uint256 public nextRecordId = 1;
    uint256 public nextAccessId = 1;

    mapping(address => bool) public verifiedProviders;

    mapping(uint256 => MedicalRecord) private medicalRecords;
    mapping(bytes32 => uint256) public recordIdByHash;
    mapping(address => uint256[]) private patientRecordIds;

    mapping(uint256 => AccessRecord) private accessRecords;

    // patient => doctor => permission
    mapping(address => mapping(address => bool)) public consent;

    mapping(address => mapping(address => uint256))
        public latestPendingRequestId;

    mapping(address => mapping(address => uint256))
        public activeStandardAccessId;

    mapping(address => mapping(address => uint256))
        public activeEmergencyAccessId;

    event AdminTransferred(
        address indexed previousAdmin,
        address indexed newAdmin
    );

    event ProviderVerificationUpdated(
        address indexed provider,
        bool verified,
        address indexed updatedBy,
        uint64 timestamp
    );

    event MedicalRecordAnchored(
        uint256 indexed recordId,
        address indexed patient,
        bytes32 indexed fileHash,
        bytes32 metadataHash,
        uint64 timestamp
    );

    event AccessRequested(
        uint256 indexed accessId,
        address indexed doctor,
        address indexed patient,
        uint64 timestamp
    );

    event AccessGranted(
        uint256 indexed accessId,
        address indexed patient,
        address indexed doctor,
        uint64 timestamp
    );

    event AccessRequestRejected(
        uint256 indexed accessId,
        address indexed patient,
        address indexed doctor,
        uint64 timestamp
    );

    event AccessRevoked(
        uint256 indexed accessId,
        address indexed patient,
        address indexed doctor,
        bool emergencyAccess,
        uint64 timestamp
    );

    event EmergencyAccessGranted(
        uint256 indexed accessId,
        address indexed doctor,
        address indexed patient,
        string reason,
        uint64 timestamp,
        uint64 expiresAt
    );

    event EmergencyAccessExpired(
        uint256 indexed accessId,
        address indexed doctor,
        address indexed patient,
        uint64 timestamp
    );

    event EmergencyAccessFlagged(
        uint256 indexed accessId,
        address indexed flaggedBy,
        uint64 timestamp
    );

    event RecordAccessed(
        uint256 indexed accessId,
        uint256 indexed recordId,
        address indexed doctor,
        address patient,
        bool emergencyAccess,
        uint64 timestamp
    );

    error Unauthorized();
    error ZeroAddress();
    error ProviderNotVerified();
    error ProviderAlreadyHasAccess();
    error PendingRequestAlreadyExists();
    error NoActiveAccess();
    error RecordNotFound();
    error AccessRecordNotFound();
    error DuplicateRecordHash();
    error InvalidFileHash();
    error InvalidEmergencyReason();
    error InvalidAccessState();
    error EmergencyAccessStillActive();
    error EmergencyAccessAlreadyActive();
    error AlreadyFlaggedForReview();
    error SelfAccessNotAllowed();

    modifier onlyAdmin() {
        if (msg.sender != admin) revert Unauthorized();
        _;
    }

    modifier onlyVerifiedProvider() {
        if (!verifiedProviders[msg.sender]) {
            revert ProviderNotVerified();
        }
        _;
    }

    constructor() {
        admin = msg.sender;
        emit AdminTransferred(address(0), msg.sender);
    }

    // ---------------------------------------------------------------------
    // Administration
    // ---------------------------------------------------------------------

    function transferAdmin(address newAdmin) external onlyAdmin {
        if (newAdmin == address(0)) revert ZeroAddress();

        address previousAdmin = admin;
        admin = newAdmin;

        emit AdminTransferred(previousAdmin, newAdmin);
    }

    function setVerifiedProvider(
        address provider,
        bool verified
    ) external onlyAdmin {
        if (provider == address(0)) revert ZeroAddress();

        verifiedProviders[provider] = verified;

        emit ProviderVerificationUpdated(
            provider,
            verified,
            msg.sender,
            _timestamp()
        );
    }

    // ---------------------------------------------------------------------
    // Medical-record hashes
    // ---------------------------------------------------------------------

    /**
     * @notice Anchors an encrypted off-chain file to Ethereum.
     * @param fileHash Hash of the encrypted medical file.
     * @param metadataHash Hash of any associated encrypted metadata.
     */
    function anchorMedicalRecord(
        bytes32 fileHash,
        bytes32 metadataHash
    ) external returns (uint256 recordId) {
        if (fileHash == bytes32(0)) revert InvalidFileHash();
        if (recordIdByHash[fileHash] != 0) revert DuplicateRecordHash();

        recordId = nextRecordId++;

        medicalRecords[recordId] = MedicalRecord({
            id: recordId,
            patient: msg.sender,
            fileHash: fileHash,
            metadataHash: metadataHash,
            createdAt: _timestamp()
        });

        recordIdByHash[fileHash] = recordId;
        patientRecordIds[msg.sender].push(recordId);

        emit MedicalRecordAnchored(
            recordId,
            msg.sender,
            fileHash,
            metadataHash,
            _timestamp()
        );
    }

    // ---------------------------------------------------------------------
    // Normal access
    // ---------------------------------------------------------------------

    function requestAccess(
        address patient
    ) external onlyVerifiedProvider returns (uint256 accessId) {
        if (patient == address(0)) revert ZeroAddress();
        if (patient == msg.sender) revert SelfAccessNotAllowed();

        if (consent[patient][msg.sender]) {
            revert ProviderAlreadyHasAccess();
        }

        uint256 pendingId =
            latestPendingRequestId[patient][msg.sender];

        if (
            pendingId != 0 &&
            accessRecords[pendingId].status == AccessStatus.Pending
        ) {
            revert PendingRequestAlreadyExists();
        }

        accessId = nextAccessId++;

        accessRecords[accessId] = AccessRecord({
            id: accessId,
            patient: patient,
            doctor: msg.sender,
            accessType: AccessType.Standard,
            status: AccessStatus.Pending,
            requestedAt: _timestamp(),
            grantedAt: 0,
            expiresAt: 0,
            flaggedForReview: false,
            reasonHash: bytes32(0)
        });

        latestPendingRequestId[patient][msg.sender] = accessId;

        emit AccessRequested(
            accessId,
            msg.sender,
            patient,
            _timestamp()
        );
    }

    /**
     * @notice Grants access using the doctor's address.
     * If a pending request exists, that request is approved.
     * Otherwise, a direct patient-created permission is recorded.
     */
    function grantAccess(
        address doctor
    ) external returns (uint256 accessId) {
        if (doctor == address(0)) revert ZeroAddress();
        if (doctor == msg.sender) revert SelfAccessNotAllowed();
        if (!verifiedProviders[doctor]) revert ProviderNotVerified();

        if (consent[msg.sender][doctor]) {
            revert ProviderAlreadyHasAccess();
        }

        uint64 currentTime = _timestamp();
        accessId = latestPendingRequestId[msg.sender][doctor];

        if (
            accessId != 0 &&
            accessRecords[accessId].status == AccessStatus.Pending
        ) {
            AccessRecord storage pendingRecord =
                accessRecords[accessId];

            pendingRecord.status = AccessStatus.Granted;
            pendingRecord.grantedAt = currentTime;
        } else {
            accessId = nextAccessId++;

            accessRecords[accessId] = AccessRecord({
                id: accessId,
                patient: msg.sender,
                doctor: doctor,
                accessType: AccessType.Standard,
                status: AccessStatus.Granted,
                requestedAt: currentTime,
                grantedAt: currentTime,
                expiresAt: 0,
                flaggedForReview: false,
                reasonHash: bytes32(0)
            });
        }

        consent[msg.sender][doctor] = true;
        activeStandardAccessId[msg.sender][doctor] = accessId;
        latestPendingRequestId[msg.sender][doctor] = 0;

        emit AccessGranted(
            accessId,
            msg.sender,
            doctor,
            currentTime
        );
    }

    function rejectAccessRequest(uint256 accessId) external {
        AccessRecord storage accessRecord = accessRecords[accessId];

        if (accessRecord.id == 0) revert AccessRecordNotFound();
        if (accessRecord.patient != msg.sender) revert Unauthorized();

        if (
            accessRecord.accessType != AccessType.Standard ||
            accessRecord.status != AccessStatus.Pending
        ) {
            revert InvalidAccessState();
        }

        accessRecord.status = AccessStatus.Rejected;

        if (
            latestPendingRequestId[msg.sender][accessRecord.doctor] ==
            accessId
        ) {
            latestPendingRequestId[msg.sender][accessRecord.doctor] = 0;
        }

        emit AccessRequestRejected(
            accessId,
            msg.sender,
            accessRecord.doctor,
            _timestamp()
        );
    }

    /**
     * @notice Revokes standard and active emergency access for a doctor.
     */
    function revokeAccess(address doctor) external {
        if (doctor == address(0)) revert ZeroAddress();

        bool accessChanged;
        uint64 currentTime = _timestamp();

        uint256 standardId =
            activeStandardAccessId[msg.sender][doctor];

        if (consent[msg.sender][doctor]) {
            consent[msg.sender][doctor] = false;
            activeStandardAccessId[msg.sender][doctor] = 0;
            accessChanged = true;

            if (
                standardId != 0 &&
                accessRecords[standardId].status ==
                AccessStatus.Granted
            ) {
                accessRecords[standardId].status =
                    AccessStatus.Revoked;
            }

            emit AccessRevoked(
                standardId,
                msg.sender,
                doctor,
                false,
                currentTime
            );
        }

        uint256 emergencyId =
            activeEmergencyAccessId[msg.sender][doctor];

        if (
            emergencyId != 0 &&
            accessRecords[emergencyId].status ==
            AccessStatus.Granted
        ) {
            AccessRecord storage emergencyRecord =
                accessRecords[emergencyId];

            activeEmergencyAccessId[msg.sender][doctor] = 0;
            accessChanged = true;

            if (emergencyRecord.expiresAt <= currentTime) {
                emergencyRecord.status = AccessStatus.Expired;

                emit EmergencyAccessExpired(
                    emergencyId,
                    doctor,
                    msg.sender,
                    currentTime
                );
            } else {
                emergencyRecord.status = AccessStatus.Revoked;

                emit AccessRevoked(
                    emergencyId,
                    msg.sender,
                    doctor,
                    true,
                    currentTime
                );
            }
        }

        if (!accessChanged) revert NoActiveAccess();
    }

    // ---------------------------------------------------------------------
    // Emergency access
    // ---------------------------------------------------------------------

    function requestEmergencyAccess(
        address patient,
        string calldata reason
    ) external onlyVerifiedProvider returns (uint256 accessId) {
        if (patient == address(0)) revert ZeroAddress();
        if (patient == msg.sender) revert SelfAccessNotAllowed();

        uint256 reasonLength = bytes(reason).length;

        if (
            reasonLength == 0 ||
            reasonLength > MAX_EMERGENCY_REASON_LENGTH
        ) {
            revert InvalidEmergencyReason();
        }

        uint64 currentTime = _timestamp();
        uint256 previousId =
            activeEmergencyAccessId[patient][msg.sender];

        if (
            previousId != 0 &&
            accessRecords[previousId].status ==
            AccessStatus.Granted
        ) {
            if (
                accessRecords[previousId].expiresAt >
                currentTime
            ) {
                revert EmergencyAccessAlreadyActive();
            }

            accessRecords[previousId].status =
                AccessStatus.Expired;

            emit EmergencyAccessExpired(
                previousId,
                msg.sender,
                patient,
                currentTime
            );
        }

        accessId = nextAccessId++;
        uint64 expiresAt =
            currentTime + EMERGENCY_ACCESS_DURATION;

        accessRecords[accessId] = AccessRecord({
            id: accessId,
            patient: patient,
            doctor: msg.sender,
            accessType: AccessType.Emergency,
            status: AccessStatus.Granted,
            requestedAt: currentTime,
            grantedAt: currentTime,
            expiresAt: expiresAt,
            flaggedForReview: false,
            reasonHash: keccak256(bytes(reason))
        });

        activeEmergencyAccessId[patient][msg.sender] = accessId;

        emit EmergencyAccessGranted(
            accessId,
            msg.sender,
            patient,
            reason,
            currentTime,
            expiresAt
        );
    }

    function flagForReview(uint256 accessId) external {
        AccessRecord storage accessRecord = accessRecords[accessId];

        if (accessRecord.id == 0) revert AccessRecordNotFound();

        if (
            msg.sender != accessRecord.patient &&
            msg.sender != admin
        ) {
            revert Unauthorized();
        }

        if (accessRecord.accessType != AccessType.Emergency) {
            revert InvalidAccessState();
        }

        if (accessRecord.flaggedForReview) {
            revert AlreadyFlaggedForReview();
        }

        accessRecord.flaggedForReview = true;

        emit EmergencyAccessFlagged(
            accessId,
            msg.sender,
            _timestamp()
        );
    }

    /**
     * @notice Updates an expired emergency record's stored status.
     * Anyone can call this after its expiry time.
     */
    function expireEmergencyAccess(uint256 accessId) external {
        AccessRecord storage accessRecord = accessRecords[accessId];

        if (accessRecord.id == 0) revert AccessRecordNotFound();

        if (
            accessRecord.accessType != AccessType.Emergency ||
            accessRecord.status != AccessStatus.Granted
        ) {
            revert InvalidAccessState();
        }

        if (accessRecord.expiresAt > block.timestamp) {
            revert EmergencyAccessStillActive();
        }

        accessRecord.status = AccessStatus.Expired;

        if (
            activeEmergencyAccessId[
                accessRecord.patient
            ][accessRecord.doctor] == accessId
        ) {
            activeEmergencyAccessId[
                accessRecord.patient
            ][accessRecord.doctor] = 0;
        }

        emit EmergencyAccessExpired(
            accessId,
            accessRecord.doctor,
            accessRecord.patient,
            _timestamp()
        );
    }

    // ---------------------------------------------------------------------
    // File-access verification and audit
    // ---------------------------------------------------------------------

    function hasAccess(
        address patient,
        address doctor
    ) public view returns (bool) {
        if (!verifiedProviders[doctor]) return false;

        if (consent[patient][doctor]) return true;

        uint256 emergencyId =
            activeEmergencyAccessId[patient][doctor];

        if (emergencyId == 0) return false;

        AccessRecord storage emergencyRecord =
            accessRecords[emergencyId];

        return (
            emergencyRecord.status == AccessStatus.Granted &&
            emergencyRecord.expiresAt > block.timestamp
        );
    }

    /**
     * @notice Creates the audit event when a doctor accesses a record.
     * The backend should call this before releasing the encrypted file.
     */
    function logRecordAccess(
        address patient,
        uint256 recordId
    ) external onlyVerifiedProvider returns (uint256 accessId) {
        MedicalRecord storage medicalRecord =
            medicalRecords[recordId];

        if (medicalRecord.id == 0) revert RecordNotFound();
        if (medicalRecord.patient != patient) {
            revert Unauthorized();
        }

        bool emergencyAccess;

        if (consent[patient][msg.sender]) {
            accessId =
                activeStandardAccessId[patient][msg.sender];
        } else {
            accessId =
                activeEmergencyAccessId[patient][msg.sender];

            AccessRecord storage emergencyRecord =
                accessRecords[accessId];

            if (
                accessId == 0 ||
                emergencyRecord.status !=
                AccessStatus.Granted ||
                emergencyRecord.expiresAt <= block.timestamp
            ) {
                revert NoActiveAccess();
            }

            emergencyAccess = true;
        }

        emit RecordAccessed(
            accessId,
            recordId,
            msg.sender,
            patient,
            emergencyAccess,
            _timestamp()
        );
    }

    // ---------------------------------------------------------------------
    // Read functions
    // ---------------------------------------------------------------------

    function getMedicalRecord(
        uint256 recordId
    ) external view returns (MedicalRecord memory) {
        if (medicalRecords[recordId].id == 0) {
            revert RecordNotFound();
        }

        return medicalRecords[recordId];
    }

    function getAccessRecord(
        uint256 accessId
    ) external view returns (AccessRecord memory) {
        if (accessRecords[accessId].id == 0) {
            revert AccessRecordNotFound();
        }

        return accessRecords[accessId];
    }

    function getPatientRecordIds(
        address patient
    ) external view returns (uint256[] memory) {
        return patientRecordIds[patient];
    }

    function _timestamp() internal view returns (uint64) {
        return uint64(block.timestamp);
    }
}