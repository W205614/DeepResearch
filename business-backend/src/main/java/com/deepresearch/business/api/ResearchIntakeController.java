package com.deepresearch.business.api;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.nio.charset.StandardCharsets;
import com.deepresearch.business.security.*;
import com.deepresearch.business.service.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

@RestController
public class ResearchIntakeController {
    private final JdbcTemplate jdbc;private final WorkspaceAccess access;private final ResearchCommandService commands;private final ObjectMapper mapper;
    public ResearchIntakeController(JdbcTemplate jdbc,WorkspaceAccess access,ResearchCommandService commands,ObjectMapper mapper){this.jdbc=jdbc;this.access=access;this.commands=commands;this.mapper=mapper;}
    public record Scope(@NotBlank @Size(max=150) String time,@NotBlank @Size(max=150) String region,@NotBlank @Size(max=150) String object,@NotBlank @Size(max=300) String deliverable){}
    private WorkspaceIdentity identity(Jwt jwt,String workspace){var i=access.resolve(jwt,workspace);access.require(i,"admin","researcher");return i;}
    private Map<String,Object> owned(WorkspaceIdentity i,String id){
        var rows=jdbc.queryForList("SELECT * FROM research_intakes WHERE id=? AND workspace_id=? AND subject=? FOR UPDATE",id,i.workspaceId(),i.subject());
        if(rows.isEmpty())throw new ResponseStatusException(HttpStatus.NOT_FOUND,"待确认研究不存在");
        var row=rows.getFirst();
        if(((Number)row.get("expires_at")).longValue()<=Instant.now().getEpochSecond())throw new ResponseStatusException(HttpStatus.CONFLICT,"研究确认已过期，请重新提交");
        return row;
    }
    @PostMapping("/api/research/intakes") @Transactional
    Map<String,Object> preflight(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@Valid @RequestBody Requests.Run request){
        var i=identity(jwt,workspace);
        if(!ResearchIntakePolicy.needsClarification(request))return Map.of("required",false);
        String id=UUID.nameUUIDFromBytes((i.workspaceId()+"|"+i.subject()+"|"+request.getClientRequestId()).getBytes(StandardCharsets.UTF_8)).toString();
        if(jdbc.queryForList("SELECT id FROM research_intakes WHERE id=?",id).isEmpty()
            && jdbc.queryForObject("SELECT count(*) FROM research_intakes WHERE workspace_id=? AND subject=? AND status IN ('pending','confirmed') AND expires_at>?",Integer.class,i.workspaceId(),i.subject(),Instant.now().getEpochSecond())>=20)
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS,"待确认研究已达到上限，请先取消或完成已有研究");
        jdbc.update("INSERT INTO research_intakes(id,workspace_id,subject,request_json,status,expires_at,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?,?) ON CONFLICT(id) DO NOTHING",
            id,i.workspaceId(),i.subject(),mapper.writeValueAsString(request),Instant.now().getEpochSecond()+3600,WorkspaceAccess.now(),WorkspaceAccess.now());
        var row=owned(i,id);var stored=mapper.readValue(String.valueOf(row.get("request_json")),Requests.Run.class);
        if(!ResearchIntakePolicy.sameRequest(stored,request))throw new ResponseStatusException(HttpStatus.CONFLICT,"同一个请求编号不能确认不同研究内容");
        return Map.of("required",true,"id",id,"status",row.get("status"),"expires_at",row.get("expires_at"),"topic",stored.normalizedTopic());
    }
    @GetMapping("/api/research/intakes")
    List<Map<String,Object>> pending(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace){
        var i=identity(jwt,workspace);return jdbc.queryForList("SELECT id,request_json,answers_json,status,expires_at FROM research_intakes WHERE workspace_id=? AND subject=? AND status IN ('pending','confirmed') AND expires_at>? ORDER BY created_at DESC LIMIT 20",i.workspaceId(),i.subject(),Instant.now().getEpochSecond());
    }
    @PutMapping("/api/research/intakes/{id}") @Transactional
    Map<String,Boolean> edit(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@PathVariable String id,@Valid @RequestBody Scope body){
        var i=identity(jwt,workspace);var row=owned(i,id);
        if(!"pending".equals(row.get("status")))throw new ResponseStatusException(HttpStatus.CONFLICT,"当前确认状态不能编辑");
        jdbc.update("UPDATE research_intakes SET answers_json=?,updated_at=? WHERE id=?",mapper.writeValueAsString(body),WorkspaceAccess.now(),id);return Map.of("ok",true);
    }
    @PostMapping("/api/research/intakes/{id}/confirm") @Transactional
    Map<String,Object> confirm(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@PathVariable String id,@Valid @RequestBody Scope body){
        var i=identity(jwt,workspace);commands.lockIntakeAdmission(i);var row=owned(i,id);
        if("consumed".equals(row.get("status")))return commands.create(i,ResearchIntakePolicy.confirmedRequest(mapper,row));
        if(!"pending".equals(row.get("status")))throw new ResponseStatusException(HttpStatus.CONFLICT,"当前确认状态不能提交");
        jdbc.update("UPDATE research_intakes SET answers_json=?,status='confirmed',updated_at=? WHERE id=?",mapper.writeValueAsString(body),WorkspaceAccess.now(),id);
        row.put("answers_json",mapper.writeValueAsString(body));row.put("status","confirmed");
        var request=ResearchIntakePolicy.confirmedRequest(mapper,row);
        if(request.normalizedTopic().length()>4000)throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_ENTITY,"确认后的主题过长，请缩短原始主题");
        var run=commands.create(i,request);
        jdbc.update("UPDATE research_intakes SET status='consumed',run_id=?,updated_at=? WHERE id=?",run.get("id"),WorkspaceAccess.now(),id);
        access.audit(i,"research.scope.confirm","research_intake",id);return run;
    }
    @PostMapping("/api/research/intakes/{id}/cancel") @Transactional
    Map<String,Boolean> cancel(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@PathVariable String id){
        var i=identity(jwt,workspace);owned(i,id);jdbc.update("UPDATE research_intakes SET status='cancelled',updated_at=? WHERE id=? AND status IN ('pending','confirmed')",WorkspaceAccess.now(),id);access.audit(i,"research.scope.cancel","research_intake",id);return Map.of("ok",true);
    }
}
